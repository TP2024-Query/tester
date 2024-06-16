import json
import logging
import os
import random
import string
import uuid
from difflib import HtmlDiff
from json import JSONDecodeError
from time import sleep
import networkx as nx

import docker
import psycopg
import redis
import sentry_sdk
from django.conf import settings
from django.db import connection
from django.utils.translation import gettext as _
from docker.errors import ImageNotFound
from requests import Timeout, Session, Request

from typing import List, Optional, TypedDict, Dict, Any

from apps.core.models import TaskRecord, Task


class ScenarioResultJson(TypedDict):
    id: uuid.UUID
    url: str
    status: str
    status_code: int
    ignored_properties: Optional[List[str]]
    messages: Optional[List[str]]
    diff: Optional[str]
    additional_data: Optional[Dict[str, Any]]
    duration: str
    response: str


class ScenarioJson(TypedDict):
    id: uuid.UUID
    url: str
    status_code: int
    ignored_properties: Optional[List[str]]
    depends_on: Optional[List[uuid.UUID]]
    body: Optional[Dict[str, Any]]
    method: Optional[str]
    response: str


class TaskJson(TypedDict):
    id: uuid.UUID
    docker_image: str
    db_name: str
    status: str
    scenarios: List[ScenarioJson]


class TaskResultJson(TypedDict):
    id: uuid.UUID
    docker_image: str
    db_name: str
    status: str
    message: str
    output: str
    scenario_results: List[ScenarioResultJson]


class BasicJob:
    def __init__(self, task_json: str, r: redis.Redis):
        self.redis = r
        self._task: TaskJson = json.loads(task_json)
        self._taskResult = {
            "id": self._task["id"],
            "db_name": self._task["db_name"],
            "docker_image": self._task["docker_image"],
            "status": self._task["status"],
            "scenario_results": []
        }
        self._database_name = "dbs_tmp_" + "".join(random.choices(string.ascii_letters, k=10)).lower()
        self._database_password = "".join(random.choices(string.ascii_letters, k=10)).lower()

    def prepare(self):
        # Create temporary user
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE USER {self._database_name} WITH CREATEDB ENCRYPTED PASSWORD '{self._database_password}';"
            )
            cursor.execute(f"GRANT CONNECT ON DATABASE {self._task['db_name']} TO {self._database_name};")
            connection.commit()

        conn = psycopg.connect(
            host=settings.DATABASES["default"]["HOST"],
            dbname=self._task['db_name'],
            user=settings.DATABASES["default"]["USER"],
            password=settings.DATABASES["default"]["PASSWORD"],
            port=settings.DATABASES["default"]["PORT"],
        )

        with conn.cursor() as cursor:
            cursor.execute(f"GRANT USAGE ON SCHEMA public TO {self._database_name};")
            cursor.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {self._database_name};")
            conn.commit()
        conn.close()

    def run(self):


        client = docker.from_env()
        params = {
            "image": self._task['docker_image'],
            "detach": True,
            "environment": {
                "NAME": "Arthur",
                "DATABASE_HOST": settings.DATABASES["default"]["HOST"],
                "DATABASE_PORT": settings.DATABASES["default"]["PORT"],
                "DATABASE_NAME": self._task['db_name'],
                "DATABASE_USER": self._database_name,
                "DATABASE_PASSWORD": self._database_password,
            },
            "name": self._task['id'],
            "privileged": False,
            "network": settings.DBS_DOCKER_NETWORK,
            "extra_hosts": {"host.docker.internal": "host-gateway", "docker.for.mac.localhost": "host-gateway"},
        }

        if not os.getenv("DOCKER"):
            params["ports"] = {"8000/tcp": "9050"}

        container = client.containers.run(**params)
        sleep(15)
        container.reload()

        scenario_results = []
        g = nx.DiGraph()
        sorted_scenario_ids=[]
        for scenario in self._task['scenarios']:
            g.add_node(scenario['id'])
            if 'depends_on' in scenario:
                for dep in scenario['depends_on']:
                    g.add_edge(dep, scenario['id'])  # depends_on indicates the direction of the edge

        try:
            sorted_scenario_ids = list(nx.algorithms.dag.topological_sort(g))
        except nx.NetworkXUnfeasible:
            print("Dependency Error: There are one or more cycles in your scenario dependencies. Can't resolve "
                  "dependency chain.")
            sorted_scenario_ids = []
            self._taskResult["message"] = "Dependency Error: There are one or more cycles in your scenario dependencies."


            container.stop(timeout=5)
            sleep(5)
            container.remove(force=True)
            try:
                client.images.get(self._task['docker_image']).remove(force=True)
            except ImageNotFound:
                pass

            raise Exception("Circular dependency found! Halting execution.")

        # In place sort to refill the scenarios list with sorted entries
        self._task['scenarios'].sort(
            key=lambda x: sorted_scenario_ids.index(x['id']) if x['id'] in sorted_scenario_ids else float('inf'))


        for scenario in self._task['scenarios']:
            url: string
            if os.getenv("DOCKER"):
                container_ip = container.attrs["NetworkSettings"]["Networks"][settings.DBS_DOCKER_NETWORK]["IPAddress"]
                url = f"http://{container_ip}:8000{scenario['url']}"
            else:
                url = f"http://127.0.0.1:9050{scenario['url']}"

            record: ScenarioResultJson = {
                'id': scenario['id'],
                'url': scenario['url'],
                'status': '',
                'status_code': scenario['status_code'],
                'ignored_properties': scenario['ignored_properties'],
                'messages': [],
                'diff': '',
                'additional_data': {},
                'duration': -1,
                'response': ''
            }

            if 'depends_on' in scenario:
                dependent_scenario_result = next((r for r in scenario_results if r['id'] == scenario['depends_on']),
                                                 None)
                if dependent_scenario_result and dependent_scenario_result['status'] != TaskRecord.Status.OK:
                    record['status'] = TaskRecord.Status.SKIPPED
                    record['messages'].append('Scenario skipped')
                    scenario_results.append(record)
                    continue

            s = Session()
            req = Request(
                method=scenario['method'],
                url=url,
            )
            if scenario['body']:
                req.json = scenario['body']
            try:
                r = s.send(req.prepare(), timeout=settings.DBS_TESTER_TIMEOUT)
            except Timeout as e:
                record['status'] = TaskRecord.Status.TIMEOUT
                record['messages'].append(str(e))
                continue
            except BaseException as e:
                record['status'] = TaskRecord.Status.ERROR
                record['messages'].append(str(e))
                continue

            record['duration'] = str(r.elapsed)
            record['response'] = str(r.content)

            if r.status_code != scenario['status_code']:
                record['status'] = TaskRecord.Status.INVALID
                record['messages'].append(
                    f"Invalid HTTP Status code (received={r.status_code}, expected={scenario['status_code']})"
                )

            if r.content:
                try:
                    response = r.json()
                    try:
                        record['response'] = json.dumps(
                            {key: response[key] for key in response if
                             key not in (scenario['ignored_properties'] or [])},
                            sort_keys=True,
                            indent=4,
                        )
                    except TypeError:
                        record['response'] = json.dumps(response, sort_keys=True, indent=4)

                    valid_response = json.dumps(scenario['response'], sort_keys=True, indent=4)

                    if record['response'] != valid_response:
                        valid_lines = valid_response.splitlines(keepends=True)
                        response_lines = record['response'].splitlines(keepends=True)
                        d = HtmlDiff()
                        record['diff'] = d.make_table(
                            valid_lines,
                            response_lines,
                            fromdesc=_("Valid response"),
                            todesc=_("Your response"),
                        )
                        record["status"] = TaskRecord.Status.INVALID
                        record["messages"].append(f"JSON Mismatch")

                except JSONDecodeError as e:
                    record["status"] = TaskRecord.Status.INVALID
                    record["messages"].append("Invalid JSON")
                    record["additional_data"]["exception"] = str(e)
            record["status"] = TaskRecord.Status.OK
            scenario_results.append(record)
        self._taskResult["status"] = Task.Status.DONE
        self._taskResult['output'] = container.logs().decode()
        self._taskResult["message"]= 'null'
        self._taskResult['scenario_results'] = scenario_results
        # replacing the self._task.status = Task.Status.DONE and self._task.save() calls
        self.redis.lpush('scenario_results_queue', json.dumps(self._taskResult))

        # Cleanup
        container.stop(timeout=5)
        sleep(5)
        container.remove(force=True)
        try:
            client.images.get(self._task['docker_image']).remove(force=True)
        except ImageNotFound:
            pass

    def cleanup(self):
        with connection.cursor() as cursor:
            cursor.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {self._database_name};")
            cursor.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {self._database_name};")
            cursor.execute(f"REVOKE CONNECT ON DATABASE {self._task['db_name']} FROM {self._database_name};")
            cursor.execute(f"DROP DATABASE IF EXISTS {self._database_name};")
            cursor.execute(f"DROP USER IF EXISTS {self._database_name};")
            connection.commit()

    def execute(self):
        try:
            task_status = self._task.get('status')
            if task_status != Task.Status.PENDING:
                logging.warning("Task is already done! Skipping.")
                return
            self.prepare()
            try:
                self.run()
                print("Task is done!", flush=True)
            except (BaseException, Exception, TypeError) as e:
                self._taskResult['status'] = Task.Status.FAILED
                self._taskResult['message'] = str(e)
                self.redis.lpush('scenario_results_queue', json.dumps(self._taskResult))

                with sentry_sdk.push_scope() as scope:
                    scope.set_extra("task", self._task['id'])
                    scope.set_extra("image", self._task['docker_image'])
                    sentry_sdk.capture_exception(e)
                logging.error(f"An exception occurred: {e}")
                self.cleanup()

        except Exception as e:
            logging.error(f"An exception occurred: {e}")
            self._taskResult['status'] = Task.Status.FAILED
            self._taskResult['message'] = str(e)
            self.redis.lpush('scenario_results_queue', json.dumps(self._taskResult))
        finally:
            self.cleanup()


def basic_job(self) -> Optional[dict]:
    return BasicJob.execute(self=self)
