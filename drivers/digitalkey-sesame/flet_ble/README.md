# flet-ble

Minimal BLE central service for Flet apps (Android / iOS / macOS / Linux / Windows via
[flutter_blue_plus](https://pub.dev/packages/flutter_blue_plus)).

It exposes just enough for a GATT client: scan (with manufacturer-data), connect,
discover, write, notify, disconnect. Bytes cross the bridge as hex strings.
