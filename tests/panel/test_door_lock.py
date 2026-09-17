from circuitmatter import interaction_model

from digitalkey.panel.door_lock import DoorLockDevice, DoorState, Feature, LockState, ONLY_NORMAL


class Path:
    def __init__(self, cmd):
        self.Endpoint, self.Cluster, self.Command = 1, 0x0101, cmd


def test_device_and_cluster_shape():
    calls = []
    d = DoorLockDevice("玄関", lambda: calls.append("lock") or True, lambda: calls.append("unlock") or True)
    assert d.descriptor.DeviceTypeList[0].DeviceType == 0x000A
    assert 0x0101 in [srv.CLUSTER_ID for srv in d.servers]
    c = d.lock
    assert c.feature_map == Feature.DOOR_POSITION_SENSOR and c.cluster_revision == 7
    assert c.LockState == LockState.LOCKED and c.SupportedOperatingModes == ONLY_NORMAL
    assert sorted(c.accepted_command_list) == [0x00, 0x01]
    assert 0x0000 in c.attribute_list and 0x0003 in c.attribute_list and 0x0026 in c.attribute_list


def test_commands_go_to_core_and_mirror():
    calls = []
    d = DoorLockDevice("玄関", lambda: calls.append("lock") or True, lambda: calls.append("unlock") or True)
    r = d.lock.invoke(None, Path(0x01), {})  # 復号済みの引数(暗証番号なし)
    assert calls == ["unlock"] and d.lock.LockState == LockState.UNLOCKED
    assert r == interaction_model.StatusCode.SUCCESS
    d.lock.invoke(None, Path(0x00), {})
    assert calls == ["unlock", "lock"] and d.lock.LockState == LockState.LOCKED
    d.mirror(False, True)
    assert d.lock.LockState == LockState.UNLOCKED and d.lock.DoorState == DoorState.DOOR_OPEN


def test_refused_command_keeps_state():
    d = DoorLockDevice("玄関", lambda: False, lambda: False)
    d.lock.invoke(None, Path(0x01), {})
    assert d.lock.LockState == LockState.LOCKED
