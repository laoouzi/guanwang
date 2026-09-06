import tempfile
import unittest
from pathlib import Path

from laoban.cli import main


class TestCliInit(unittest.TestCase):
    def test_init_creates_dirs(self):
        tmp = tempfile.mkdtemp()
        rc = main(["init", "--root", tmp])
        self.assertEqual(rc, 0)
        self.assertTrue((Path(tmp) / "tasks").exists())
        self.assertTrue((Path(tmp) / "employees").exists())


if __name__ == "__main__":
    unittest.main()
