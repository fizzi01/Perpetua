//  Perpetua - open-source and cross-platform KVM software.
//  Copyright (c) 2026 Federico Izzi.
//
//  This program is free software: you can redistribute it and/or modify
//  it under the terms of the GNU General Public License as published by
//  the Free Software Foundation, either version 3 of the License, or
//  (at your option) any later version.
//
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//  GNU General Public License for more details.
//
//  You should have received a copy of the GNU General Public License
//  along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// Probe only: org.perpetua.ProbePointer.GetPointer() -> (x, y) from
// global.get_pointer(), the compositor's own pointer position.

import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'org.perpetua.ProbePointer';
const OBJECT_PATH = '/org/perpetua/ProbePointer';
const IFACE = `
<node>
  <interface name="org.perpetua.ProbePointer">
    <method name="GetPointer">
      <arg type="i" direction="out" name="x"/>
      <arg type="i" direction="out" name="y"/>
    </method>
  </interface>
</node>`;

export default class PointerProbeExtension extends Extension {
    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._impl.export(Gio.DBus.session, OBJECT_PATH);
        this._ownerId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        Gio.bus_unown_name(this._ownerId);
        this._impl.unexport();
        this._impl = null;
    }

    GetPointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }
}
