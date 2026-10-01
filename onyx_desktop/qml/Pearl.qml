import QtQuick
Item {
    id: pearl
    property bool animate: false
    property real glow: .5
    property string paletteName: client.appearance.livePalette || "pearl"
    property real speed: client.appearance.liveSpeed || 10
    property real envelope: 0
    property real breathe: 0
    readonly property bool motionAllowed: animate && visible && !client.reducedMotion
    property color spectrumColor: "#C7B7ED"
    property color edgeColor: paletteName === "cool" ? "#9DDEFF" : paletteName === "lilac" ? "#C3ACFF" : paletteName === "custom" ? Tokens.accent : "#DADAE4"
    Rectangle {
        id: rim
        // A faint native gradient sits behind the unchanged canonical PNG.
        // No texture readback, blur, offscreen layer or additional asset decode.
        anchors.centerIn: parent; width: parent.width*.78; height: width; radius: width/2
        property color tint: pearl.paletteName === "pearl" ? pearl.spectrumColor : pearl.edgeColor
        opacity: pearl.paletteName === "pearl" ? (pearl.motionAllowed ? .12*pearl.glow : 0) : .10*pearl.glow
        gradient: Gradient {
            GradientStop { position: 0; color: "transparent" }
            GradientStop { position: .24; color: Qt.rgba(rim.tint.r,rim.tint.g,rim.tint.b,.7) }
            GradientStop { position: .50; color: "transparent" }
            GradientStop { position: .76; color: Qt.rgba(rim.tint.r,rim.tint.g,rim.tint.b,.6) }
            GradientStop { position: 1; color: "transparent" }
        }
    }
    Image {
        id: art; anchors.fill: parent; source: "../assets/pearl.png"; cache: true
        fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true
        scale: client.reducedMotion ? 1 : 1+pearl.breathe*.025*pearl.glow+Math.min(.06,pearl.envelope*.06)
        opacity: client.reducedMotion ? 1 : .94+.06*pearl.breathe
    }
    SequentialAnimation on breathe {
        running: pearl.motionAllowed; loops: Animation.Infinite
        NumberAnimation { from: 0; to: 1; duration: pearl.speed*1000; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0; duration: pearl.speed*1000; easing.type: Easing.InOutSine }
    }
    SequentialAnimation on spectrumColor {
        objectName: "spectrumDrift"
        running: pearl.motionAllowed && pearl.paletteName === "pearl"; loops: Animation.Infinite
        ColorAnimation { to: "#ADD8EA"; duration: 45000; easing.type: Easing.InOutSine }
        ColorAnimation { to: "#EBC0D9"; duration: 45000; easing.type: Easing.InOutSine }
        ColorAnimation { to: "#C7B7ED"; duration: 45000; easing.type: Easing.InOutSine }
    }
}
