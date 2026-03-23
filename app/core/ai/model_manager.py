class ModelManager:

    def __init__(self):

        self.model_version = 1

    def update(self):

        self.model_version += 1

    def version(self):

        return self.model_version
