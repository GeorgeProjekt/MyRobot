class TrainingBuffer:

    def __init__(self):

        self.data = []

    def add(self, features, label):

        self.data.append(
            {
                "features": features,
                "label": label
            }
        )

        if len(self.data) > 5000:

            self.data.pop(0)

    def dataset(self):

        return self.data
