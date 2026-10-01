pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtQml.Models

Item {
    id: root
    required property var bridge
    property bool active: root.bridge.data.edge === true
    property bool captureHidden: root.bridge.data.captureHidden === true
    property bool calm: root.bridge.data.calmMode !== false
    property real level: active && !captureHidden && !calm ? (root.bridge.data.amplitude || 0) : 0
    property real fade: active ? 1 : 0
    Behavior on fade {
        NumberAnimation {
            duration: root.active ? (root.bridge.data.appearMs || 420) : (root.bridge.data.fadeMs || 650)
            easing.type: Easing.InOutSine
        }
    }
    Behavior on level {
        enabled: root.active && !root.captureHidden && !root.calm
        NumberAnimation { duration: 600; easing.type: Easing.InOutSine }
    }
    // No perpetual frame timer, travelling wave, or chunk-driven opacity.
    // Settled calm mode does not schedule redraws; capture does not restart fade.
    Instantiator {
        model: root.bridge.screens
        delegate: Window {
            id: edgeWindow
            required property var modelData
            transientParent: null
            objectName: "edgeWindow"
            title: "Onyx Edge Presence"
            color: "transparent"
            flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
            x: modelData.x; y: modelData.y
            width: modelData.width; height: modelData.height
            visible: root.fade > .001 && !root.captureHidden
            Component.onCompleted: root.bridge.registerWindow(edgeWindow, modelData.name)
            property real haloSize: root.bridge.data.halo || 64
            // Nonoverlapping strips share monitor-local falloff. Corners never
            // add two bright contributions and the center stays transparent.
            Repeater {
                model: 4
                ShaderEffect {
                    required property int index
                    x: index === 3 ? edgeWindow.width-edgeWindow.haloSize : 0
                    y: index === 0 ? 0 : (index === 1 ? edgeWindow.height-edgeWindow.haloSize : edgeWindow.haloSize)
                    width: index < 2 ? edgeWindow.width : edgeWindow.haloSize
                    height: index < 2 ? edgeWindow.haloSize : Math.max(0, edgeWindow.height-2*edgeWindow.haloSize)
                    opacity: root.fade * (root.bridge.data.intensity || 0)
                    property vector2d viewport: Qt.vector2d(edgeWindow.width, edgeWindow.height)
                    property vector2d origin: Qt.vector2d(x, y)
                    property vector2d extent: Qt.vector2d(width, height)
                    property real energy: root.level
                    property real halo: edgeWindow.haloSize
                    fragmentShader: "edge.frag.qsb"
                }
            }
        }
    }
}
