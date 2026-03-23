import time


class LoopRunner:

    def __init__(self, engine, interval=60):

        self.engine = engine
        self.interval = interval
        self.running = False

    def start(self, market_loader):

        self.running = True

        while self.running:

            try:

                data = market_loader()

                signals = self.engine.run_once(data)

                print("signals:", signals)

            except Exception as e:

                print("loop error:", e)

            time.sleep(self.interval)

    def stop(self):

        self.running = False
