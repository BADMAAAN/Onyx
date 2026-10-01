pragma Singleton
import QtQuick
QtObject {
    readonly property int controlWidth: 1180
    readonly property int controlHeight: 780
    readonly property int titleHeight: 36
    readonly property int contentTop: 8
    readonly property int outer: 8
    readonly property int gap: 8
    readonly property real heroInsetRatio: 0.123
    readonly property int greetingSize: 38
    readonly property int heroSize: 77
    property color background: "#000000"
    readonly property color sidebar: "#080808"
    readonly property color panel: "#101010"
    readonly property color elevated: "#181818"
    readonly property color hover: "#222222"
    readonly property color border: "#292929"
    property color text: "#F0F0F0"
    readonly property color secondary: "#9D9D9D"
    readonly property color muted: "#9D9D9D"
    property color accent: "#B8A4FF"
    readonly property int radius: 17
    readonly property int gutter: 28
    property bool compact: true
    readonly property int sidebarWidth: compact ? 196 : 210
    readonly property int rowHeight: compact ? 32 : 36
    readonly property int liveWidth: 337
    readonly property int intro: 360
    readonly property int transition: 240
    property string font: "Segoe UI"
}
