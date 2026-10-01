import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
ScrollView {
    id: page
    required property var host
    clip: true
    ColumnLayout {
        width: page.availableWidth-24; spacing: 18
        RowLayout { Pearl { Layout.preferredWidth: 36; Layout.preferredHeight: 36 } Text { text: "Onyx"; color: Tokens.text; font.pixelSize: 24 } }
        Text { Layout.fillWidth: true; text: host.t("Local chat on your hardware. Internet enables web tools and explicit downloads, never cloud inference. External applications manage their own network access."); color: Tokens.secondary; wrapMode: Text.Wrap }
        TextEdit { Layout.fillWidth: true; text: client.diagnostics+"\nOllama "+(client.modelState.version || "—"); color: Tokens.secondary; wrapMode: TextEdit.Wrap; readOnly: true; selectByMouse: true; font.pixelSize: 12 }
        Text { Layout.fillWidth: true; text: host.t("Chat answers and writes. Tools permit actions under Onyx permissions. Vision reads actual images. Embedding indexes memory and cannot chat. STT transcribes; TTS speaks. Live connects STT → selected Brain → TTS."); color: Tokens.text; wrapMode: Text.Wrap }
        Text { Layout.fillWidth: true; text: host.t("Voice components")+": "+(client.backendSettings.stt || "—")+" → "+(client.currentModel || "—")+" → "+(client.backendSettings.voice || "—"); color: Tokens.secondary; wrapMode: Text.Wrap }
        Repeater {
            model: client.models
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                ModelIdentity { brand: modelData.brand; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                Text { Layout.fillWidth: true; text: modelData.id+" · "+modelData.publisher+"\n"+modelData.role+" · "+modelData.quantization+" · "+modelData.capabilities.join(", ")+"\n"+modelData.source; color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 13 }
            }
        }
        Text { Layout.fillWidth: true; text: host.t("A context limit is not long-term memory or a promise of quality. Hardware reserves may prevent large models from loading."); color: Tokens.secondary; wrapMode: Text.Wrap }
        SoftButton { text: host.t("Licenses"); onClicked: client.openLicenses() }
    }
}
