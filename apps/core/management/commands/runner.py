import logging
import os
import signal
from multiprocessing import Pool
import concurrent.futures
import redis
from django.conf import settings
from django.core.management import BaseCommand
from apps.core.jobs import BasicJob


class Command(BaseCommand):
    help = "Job runner"
    keep_running = True

    def add_arguments(self, parser):
        # Named (optional) argument
        parser.add_argument(
            '--processes',
            dest='processes',
            type=int,
            help='Define the number of processes to use',
        )

    def handle(self, *args, **options):
        self.stdout.write("Hi. I am runner")

        def stop_handler(signum, frame):
            self.stdout.write("Caught signal, stopping...")
            self.keep_running = False

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)
        queue_settings = settings.RQ_QUEUES['default']
        r = redis.Redis(host=queue_settings['HOST'], port=queue_settings['PORT'], db=queue_settings['DB'],
                        password=queue_settings['PASSWORD'])

        processes = options['processes'] or os.cpu_count()
        print(processes)
        with concurrent.futures.ThreadPoolExecutor(max_workers=processes) as executor:
            while self.keep_running:
                payload = r.rpop('task_queue')
                if payload is not None:
                    self.stdout.write("Task taken from queue")
                    job = BasicJob(payload, r)
                    executor.submit(job.execute)

        self.stdout.write("Runner stopped")

