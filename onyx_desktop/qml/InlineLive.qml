import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Item {
    id: panel
    required property var host
    ColumnLayout {
        anchors.centerIn: parent; width: parent.width-32; spacing: 6
        Pearl {
            Layout.preferredWidth: client.appearance.liveSize || 100
            Layout.preferredHeight: Layout.preferredWidth
            Layout.alignment: Qt.AlignHCenter
            animate: panel.visible && panel.host.motion
            glow: client.glow
            envelope: client.liveActive && !client.reducedMotion ? client.voiceEnvelope : 0
            Behavior on envelope { NumberAnimation { duration: client.reducedMotion ? 0 : 100 } }
        }
        Text {
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: panel.host.t(client.liveActive ? (client.liveState === "SPEAKING" ? "Speaking" : client.liveState === "THINKING" || client.liveState === "ACTING" ? "Thinking…" : "Listening") : "Start Onyx Live")
            color: Tokens.text; font.pixelSize: 14
        }
        Text {
            Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
            text: client.voiceNotice; visible: text.length>0
            color: Tokens.secondary; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.alignment: Qt.AlignHCenter; spacing: 8
            SoftButton { objectName: "liveBackground"; visible: client.liveActive; text: panel.host.t("Continue in background"); height: 30; onClicked: client.backgroundLive() }
            SoftButton { visible: !client.liveActive; text: panel.host.t("Start Onyx Live"); enabled: client.ready && client.voiceState==="listening" && !client.dictationBusy; height: 30; onClicked: client.startLive() }
            SoftButton { objectName: "stopLive"; text: panel.host.t(client.liveActive ? "Stop Live" : "Close"); height: 30; onClicked: panel.host.closeLive() }
        }
    }
}
