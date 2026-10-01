import QtQuick
import QtQuick.Controls.Basic
ComboBox {
    id: control
    implicitHeight: 36; leftPadding: 10; rightPadding: 30
    font.pixelSize: 14
    indicator: Text { x: control.width-width-10; y: (control.height-height)/2; text: "⌄"; color: Tokens.secondary; font.pixelSize: 20 }
    background: Rectangle { color: Tokens.elevated; radius: 7; border.color: control.activeFocus ? Tokens.text : Tokens.border }
    contentItem: Text { text: control.displayText; color: Tokens.text; font: control.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
}
