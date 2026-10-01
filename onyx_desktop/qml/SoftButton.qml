import QtQuick
import QtQuick.Controls.Basic
Button {
    id: control
    implicitHeight: Tokens.compact ? 34 : 38
    implicitWidth: Math.max(40, label.implicitWidth + 28)
    font.family: Tokens.font
    font.pixelSize: 13
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    contentItem: Text {
        id: label
        text: control.text; font: control.font
        color: control.enabled ? Tokens.text : Tokens.muted
        opacity: control.enabled ? 1 : 0.5
        verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter
    }
    background: Rectangle {
        radius: 10
        color: control.down ? "#363636" : control.hovered ? Tokens.elevated : "transparent"
        border.width: control.activeFocus ? 1 : 0
        border.color: Tokens.accent
    }
}
