class PositionMonitor:

    def __init__(self):

        self.positions = []

    def add(self, position):

        self.positions.append(position)

    def get_all(self):

        return self.positions
