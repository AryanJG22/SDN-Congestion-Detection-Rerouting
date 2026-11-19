"""Stub of Person 2's forwarding layer, same interface as the contract."""


class FakeForwarding:
    def __init__(self, active="P1", valid=("P1", "P2", "P3"), verbose=False):
        self.active = active
        self.valid = list(valid)
        self.datapaths = {}
        self.calls = []          # every install_path() call, in order
        self.verbose = verbose

    def install_path(self, path_id):
        self.calls.append(path_id)
        self.active = path_id
        if self.verbose:
            print("FakeForwarding.install_path(%s)" % path_id)

    def get_active_path(self):
        return self.active

    def get_valid_paths(self):
        return list(self.valid)
