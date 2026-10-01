import QtQuick
import QtQuick.Controls.Basic
CheckBox {
    id: control
    padding: 4; spacing: 9; font.pixelSize: 14
    indicator: Rectangle {
        x: control.leftPadding; y: (control.height-height)/2; width: 18; height: 18; radius: 4
        color: control.checked ? "#dddddd" : Tokens.elevated; border.color: control.activeFocus ? Tokens.text : Tokens.border
        Text { anchors.centerIn: parent; text: "✓"; color: "#151515"; visible: control.checked; font.pixelSize: 14 }
    }
    contentItem: Text { text: control.text; font: control.font; color: Tokens.text; leftPadding: 27; verticalAlignment: Text.AlignVCenter; wrapMode: Text.Wrap }
}
