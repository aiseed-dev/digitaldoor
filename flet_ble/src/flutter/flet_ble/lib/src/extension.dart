import 'package:flet/flet.dart';

import 'ble.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case "FletBle":
        return FletBleService(control: control);
      default:
        return null;
    }
  }
}
