import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Rectangle {
    id: section
    required property string sectionKey
    required property string heading
    property string summary: ""
    property string iconName: ({"General":"settings","Appearance":"image","Models":"apps","Voice & Dictation":"mic","Live":"wave","Internet":"document","Git":"code","Data":"folder"})[sectionKey] || "settings"
    property bool searching: false
    property bool expanded: searching || !!(client.appearance.settingsExpanded || {})[sectionKey]
    default property alias content: body.data
    Layout.columnSpan: expanded && parent.columns ? parent.columns : 1
    color: Tokens.panel; border.color: Tokens.border; radius: 10
    implicitHeight: header.height+(expanded ? body.implicitHeight+24 : 0)
    Button {
        id: header; width: parent.width; height: Tokens.compact ? 62 : 70
        background: Rectangle { radius: 10; color: header.hovered ? Tokens.hover : "transparent" }
        contentItem: Item {
            Image { x: 10; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; source: "../assets/icons/"+section.iconName+".svg" }
            Text { x: 40; y: 6; width: parent.width-65; text: section.heading; color: Tokens.text; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight }
            Text { x: 40; y: 29; width: parent.width-65; text: section.summary; color: Tokens.secondary; font.pixelSize: 11; elide: Text.ElideRight }
            Text { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: section.expanded ? "⌃" : "⌄"; color: Tokens.secondary }
        }
        onClicked: client.expandSettings(section.sectionKey,!section.expanded)
    }
    ColumnLayout { id: body; x: 16; y: header.height+8; width: Math.min(760,parent.width-32); visible: section.expanded; spacing: 10 }
}
