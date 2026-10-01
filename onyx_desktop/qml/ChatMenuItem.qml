import QtQuick
import QtQuick.Controls.Basic
MenuItem {
    id: item
    property string glyph: "chat"
    property bool destructive: false
    implicitHeight: 36
    hoverEnabled: true
    Accessible.name: text
    contentItem: Item {
        Image { x: 8; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; source: "../assets/icons/"+item.glyph+".svg" }
        Text { x: 38; anchors.verticalCenter: parent.verticalCenter; text: item.text; color: item.destructive ? "#ef6666" : Tokens.text; font.pixelSize: 14 }
    }
    background: Rectangle { radius: 7; color: item.highlighted ? (item.destructive ? "#392326" : Tokens.hover) : "transparent" }
}
