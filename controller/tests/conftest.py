import pytest

from digitaldoor_controller.audit import Audit, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller


@pytest.fixture
def ctl():
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=5))
    c.set_time(1000.0, True)
    return c
