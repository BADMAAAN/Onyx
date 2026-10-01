import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window
Button {
    id: control
    property string iconName: "plus"
    property string label: ""
    property int iconSize: 22
    property bool round: true
    property bool busy: false
    hoverEnabled: true
    Accessible.name: label
    activeFocusOnTab: true
    padding: 0
    contentItem: Item {
        Image { anchors.centerIn: parent; width: control.iconSize; height: control.iconSize; sourceSize: Qt.size(width,height); source: "../assets/icons/"+control.iconName+".svg"; opacity: control.enabled ? 1 : .45 }
    }
    background: Rectangle {
        anchors.centerIn: parent
        width: control.round ? Math.min(control.width,control.height) : control.width
        height: control.round ? width : control.height
        radius: control.round ? width/2 : 8
        color: control.down ? "#363636" : control.hovered ? Tokens.hover : Tokens.elevated
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Tokens.text : control.busy ? Tokens.secondary : Tokens.border
        Behavior on color { ColorAnimation { duration: 120 } }
    }
    GraphiteToolTip { text: control.label; visible: control.hovered && control.Window.window && control.Window.window.visible && control.Window.window.visibility !== Window.Minimized }
}
