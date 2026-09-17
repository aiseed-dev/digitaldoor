import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

String _hex(List<int> bytes) =>
    bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

List<int> _unhex(String s) {
  final out = <int>[];
  for (var i = 0; i + 1 < s.length; i += 2) {
    out.add(int.parse(s.substring(i, i + 2), radix: 16));
  }
  return out;
}

/// Flet service wrapping flutter_blue_plus as a plain GATT client.
///
/// Methods (invoked from Python):
///   is_supported, adapter_state, turn_on,
///   start_scan{services, manufacturer_ids, timeout_ms, fine_location},
///   stop_scan, connect{device_id, timeout_ms}, disconnect{device_id},
///   discover_services{device_id}, write{device_id, characteristic, data, without_response},
///   set_notify{device_id, characteristic, enable}, request_mtu{device_id, mtu}
/// Events (to Python):
///   scan_result{device_id, local_name, rssi, connectable, manufacturer_data{id: hex}, service_uuids[]}
///   notify{device_id, characteristic, value(hex)}
///   connection_state{device_id, connected}
///   error(String)
class FletBleService extends FletService {
  FletBleService({required super.control});

  StreamSubscription<List<ScanResult>>? _scanSub;
  StreamSubscription<OnConnectionStateChangedEvent>? _connSub;
  final Map<String, BluetoothDevice> _devices = {};
  final Map<String, List<BluetoothService>> _services = {};
  final Map<String, StreamSubscription<List<int>>> _notifySubs = {};

  @override
  void init() {
    super.init();
    debugPrint("FletBle(${control.id}).init");
    control.addInvokeMethodListener(_invokeMethod);
    _connSub = FlutterBluePlus.events.onConnectionStateChanged.listen((e) {
      final id = e.device.remoteId.str;
      final connected = e.connectionState == BluetoothConnectionState.connected;
      debugPrint("FletBle: $id connected=$connected");
      if (!connected) {
        _dropNotifySubs(id);
        _services.remove(id);
      }
      control.triggerEvent("connection_state", {"device_id": id, "connected": connected});
    });
  }

  void _dropNotifySubs(String deviceId) {
    final keys = _notifySubs.keys.where((k) => k.startsWith("$deviceId/")).toList();
    for (final k in keys) {
      _notifySubs.remove(k)?.cancel();
    }
  }

  BluetoothDevice _device(dynamic args) {
    final id = args["device_id"].toString();
    return _devices.putIfAbsent(id, () => BluetoothDevice.fromId(id));
  }

  Future<BluetoothCharacteristic> _characteristic(dynamic args) async {
    final device = _device(args);
    final id = device.remoteId.str;
    final wanted = Guid(args["characteristic"].toString());
    var services = _services[id];
    if (services == null || services.isEmpty) {
      services = await device.discoverServices();
      _services[id] = services;
    }
    for (final s in services) {
      for (final c in s.characteristics) {
        if (c.uuid == wanted) return c;
      }
    }
    throw Exception("characteristic ${wanted.str128} not found on $id");
  }

  Map<String, dynamic> _scanResultToMap(ScanResult r) {
    final mfg = <String, String>{};
    r.advertisementData.manufacturerData.forEach((k, v) {
      mfg[k.toString()] = _hex(v);
    });
    return {
      "device_id": r.device.remoteId.str,
      "local_name": r.advertisementData.advName.isNotEmpty
          ? r.advertisementData.advName
          : r.device.platformName,
      "rssi": r.rssi,
      "connectable": r.advertisementData.connectable,
      "manufacturer_data": mfg,
      "service_uuids": r.advertisementData.serviceUuids.map((g) => g.str128).toList(),
    };
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    debugPrint("FletBle.$name($args)");
    try {
      switch (name) {
        case "is_supported":
          return await FlutterBluePlus.isSupported;

        case "adapter_state":
          {
            var state = FlutterBluePlus.adapterStateNow;
            if (state == BluetoothAdapterState.unknown) {
              try {
                state = await FlutterBluePlus.adapterState
                    .where((s) => s != BluetoothAdapterState.unknown)
                    .first
                    .timeout(const Duration(seconds: 3));
              } on TimeoutException {
                // keep "unknown"
              }
            }
            return state.name;
          }

        case "turn_on":
          if (!kIsWeb && defaultTargetPlatform == TargetPlatform.android) {
            await FlutterBluePlus.turnOn();
            return true;
          }
          return false;

        case "start_scan":
          {
            await _scanSub?.cancel();
            _scanSub = FlutterBluePlus.onScanResults.listen((results) {
              for (final r in results) {
                control.triggerEvent("scan_result", _scanResultToMap(r));
              }
            }, onError: (Object e) {
              control.triggerEvent("error", "scan: $e");
            });
            final services = ((args["services"] as List?) ?? [])
                .map((s) => Guid(s.toString()))
                .toList();
            final msd = ((args["manufacturer_ids"] as List?) ?? [])
                .map((m) => MsdFilter(int.parse(m.toString())))
                .toList();
            final timeoutMs = args["timeout_ms"];
            await FlutterBluePlus.startScan(
              withServices: services,
              withMsd: msd,
              timeout: timeoutMs != null ? Duration(milliseconds: (timeoutMs as num).toInt()) : null,
              continuousUpdates: true,
              continuousDivisor: 2,
              androidUsesFineLocation: args["fine_location"] == true,
            );
            return true;
          }

        case "stop_scan":
          await FlutterBluePlus.stopScan();
          await _scanSub?.cancel();
          _scanSub = null;
          return true;

        case "connect":
          {
            final device = _device(args);
            final timeoutMs = (args["timeout_ms"] as num?)?.toInt() ?? 20000;
            await device.connect(
              license: License.nonprofit,
              timeout: Duration(milliseconds: timeoutMs),
              mtu: null,
            );
            _services.remove(device.remoteId.str);
            return true;
          }

        case "disconnect":
          {
            final device = _device(args);
            _dropNotifySubs(device.remoteId.str);
            _services.remove(device.remoteId.str);
            await device.disconnect();
            return true;
          }

        case "is_connected":
          return _device(args).isConnected;

        case "discover_services":
          {
            final device = _device(args);
            final services = await device.discoverServices();
            _services[device.remoteId.str] = services;
            return services
                .map((s) => {
                      "uuid": s.uuid.str128,
                      "characteristics": s.characteristics
                          .map((c) => {
                                "uuid": c.uuid.str128,
                                "read": c.properties.read,
                                "write": c.properties.write,
                                "write_without_response": c.properties.writeWithoutResponse,
                                "notify": c.properties.notify,
                                "indicate": c.properties.indicate,
                              })
                          .toList(),
                    })
                .toList();
          }

        case "request_mtu":
          {
            final device = _device(args);
            return await device.requestMtu((args["mtu"] as num).toInt());
          }

        case "mtu":
          return _device(args).mtuNow;

        case "write":
          {
            final c = await _characteristic(args);
            await c.write(_unhex(args["data"].toString()),
                withoutResponse: args["without_response"] != false);
            return true;
          }

        case "read":
          {
            final c = await _characteristic(args);
            return _hex(await c.read());
          }

        case "set_notify":
          {
            final c = await _characteristic(args);
            final id = c.remoteId.str;
            final key = "$id/${c.uuid.str128}";
            final enable = args["enable"] != false;
            await _notifySubs.remove(key)?.cancel();
            if (enable) {
              _notifySubs[key] = c.onValueReceived.listen((value) {
                control.triggerEvent("notify", {
                  "device_id": id,
                  "characteristic": c.uuid.str128,
                  "value": _hex(value),
                });
              });
            }
            return await c.setNotifyValue(enable);
          }

        default:
          throw Exception("Unknown FletBle method: $name");
      }
    } catch (e) {
      debugPrint("FletBle.$name failed: $e");
      throw _FletBleException("$name: $e");
    }
  }

  @override
  void dispose() {
    debugPrint("FletBle(${control.id}).dispose()");
    control.removeInvokeMethodListener(_invokeMethod);
    _scanSub?.cancel();
    _connSub?.cancel();
    for (final s in _notifySubs.values) {
      s.cancel();
    }
    _notifySubs.clear();
    super.dispose();
  }
}

class _FletBleException implements Exception {
  final String message;
  const _FletBleException(this.message);

  @override
  String toString() => message;
}
