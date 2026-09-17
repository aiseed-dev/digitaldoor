import pytest

from digitalkey.door.audit import Audit, HmacSigner
from digitalkey.door.contacts import SimContacts
from digitalkey.door.controller import Config, Controller


@pytest.fixture
def ctl():
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=5))
    c.set_time(1000.0, True)
    return c
