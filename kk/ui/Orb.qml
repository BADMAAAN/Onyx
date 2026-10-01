import QtQuick
import QtQuick.Window
import QtQml.Models

Window {
    id: orb
    width: bridge.size; height: width
    title: "Onyx Orb"
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
    visible: bridge.data.orb === true
    property real tick: 0
    NumberAnimation on tick { from: 0; to: 36000; duration: 36000000; loops: Animation.Infinite; running: orb.visible || bridge.data.edge === true }
    property real level: bridge.data.amplitude || 0
    Behavior on level { NumberAnimation { duration: 100; easing.type: Easing.OutCubic } }
    property real phase: bridge.data.activity || 0
    Behavior on phase { NumberAnimation { duration: 600; easing.type: Easing.InOutSine } }
    ShaderEffect {
        anchors.fill: parent
        property real clock: orb.tick
        property real energy: Math.min(1, orb.level)
        property real activity: orb.phase
        property real edge: 0
        fragmentShader: "field.frag.qsb"
    }
    MouseArea {
        id: body
        anchors.fill: parent
        hoverEnabled: true
        property point origin
        property bool moving: false
        onPressed: function(mouse) { origin=Qt.point(mouse.x,mouse.y); moving=false }
        onPositionChanged: function(mouse) {
            if (pressed && !moving && Math.hypot(mouse.x-origin.x,mouse.y-origin.y)>6) {
                moving=true; bridge.dragStarted(); orb.startSystemMove()
            }
        }
        onReleased: bridge.dragEnded()
        onCanceled: bridge.dragEnded()
    }
    Rectangle {
        id: mic
        width: 28; height: 28; radius: 14
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height*.66
        color: bridge.data.state === "RECORDING" ? "#77486dc2" : "#66252e48"
        border.color: "#888ea9ec"; border.width: 1
        opacity: body.containsMouse || micMouse.containsMouse || bridge.data.state === "RECORDING" ? 1 : .58
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Rectangle { width:6; height:10; radius:3; anchors.horizontalCenter: parent.horizontalCenter; y:6; color:"#c3cee9" }
        Rectangle { width:12; height:10; radius:5; anchors.horizontalCenter: parent.horizontalCenter; y:9; color:"transparent"; border.color:"#c3cee9" }
        Rectangle { width:2; height:4; anchors.horizontalCenter: parent.horizontalCenter; y:19; color:"#c3cee9" }
        MouseArea {
            id: micMouse
            anchors.fill: parent; hoverEnabled:true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            property bool held: false
            property bool aborted: false
            property point origin
            onPressed: function(mouse) {
                held=false; aborted=false; origin=Qt.point(mouse.x,mouse.y)
                if(mouse.button===Qt.RightButton) { aborted=true; bridge.action("mic_cancel") }
                else hold.start()
            }
            onPositionChanged: function(mouse) {
                if(pressed && Math.hypot(mouse.x-origin.x,mouse.y-origin.y)>6) {
                    hold.stop(); aborted=true
                    if(held) { bridge.action("mic_cancel"); held=false }
                }
            }
            onReleased: { hold.stop(); if(!aborted) bridge.action(held ? "mic_commit" : "mic_toggle") }
            onCanceled: { hold.stop(); if(held) bridge.action("mic_cancel") }
            Timer { id:hold; interval:350; onTriggered: { if(!micMouse.aborted) { micMouse.held=true; bridge.action("mic_start") } } }
        }
    }
    Instantiator {
      model: bridge.data.edgeScreens || []
      delegate: Window {
        id: edgeWindow
        required property var modelData
        transientParent: null
        objectName: "edgeWindow"
        title: "Onyx Edge Presence"; color:"transparent"
        flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
        x: modelData.x; y: modelData.y
        width: modelData.width; height: modelData.height
        property real fade: bridge.data.edge ? 1 : 0
        Behavior on fade { NumberAnimation { duration:600; easing.type:Easing.InOutSine } }
        visible: fade > .001
        // Four narrow GPU strips; the transparent center is never shaded.
        Repeater {
            model: 4
            ShaderEffect {
                required property int index
                width: index < 2 ? edgeWindow.width : edgeWindow.height
                height: 36
                x: index < 2 ? 0 : (index === 2 ? 36 : edgeWindow.width - 36)
                y: index === 0 ? edgeWindow.height - 36 : (index === 3 ? edgeWindow.height : 0)
                rotation: index === 1 ? 180 : (index === 2 ? 90 : (index === 3 ? -90 : 0))
                transformOrigin: index === 1 ? Item.Center : Item.TopLeft
                opacity: edgeWindow.fade * (edgeWindow.modelData.active ? 1 : .82)
                property real clock: orb.tick
                property real energy: Math.min(1, orb.level)
                property real activity: orb.phase
                property real edge: 1
                fragmentShader: "field.frag.qsb"
            }
        }
      }
    }
}
