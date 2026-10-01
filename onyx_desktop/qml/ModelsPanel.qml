import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
ColumnLayout {
    id: panel
    required property var host
    spacing: 10
    Dialog {
        id: modelDetails; property var row: ({}); title: host.t("Model details"); modal: true; width: Math.min(440,panel.width)
        background: Rectangle { color: Tokens.panel; border.color: Tokens.border; radius: 10 }
        standardButtons: Dialog.Close
        contentItem: ColumnLayout {
            Text { text: modelDetails.row.id || ""; color: Tokens.text; font.pixelSize: 16 }
            Text { Layout.fillWidth: true; text: host.t(modelDetails.row.chat ? "Chat models answer messages and continue conversations." : "Specialized model; unavailable for chat."); color: Tokens.secondary; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: (modelDetails.row.capabilities || []).join(", ")+"\n"+(modelDetails.row.family || modelDetails.row.brand || ""); color: Tokens.secondary; wrapMode: Text.Wrap }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Text { Layout.fillWidth: true; text: host.t("Installed models"); color: Tokens.text }
        SoftButton { text: host.t("Refresh"); onClicked: client.refreshModels() }
    }
    Text { Layout.fillWidth: true; text: host.t(client.modelState.error ? "Models are temporarily unavailable" : "Installed, selected and loaded are different states. One model runs at a time."); color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
    Repeater {
        model: client.models
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true; implicitHeight: details.implicitHeight+20; color: Tokens.elevated; radius: 8
            RowLayout {
                x: 10; y: 10; width: parent.width-20
                ModelIdentity { Layout.preferredWidth: 25; Layout.preferredHeight: 25; Layout.alignment: Qt.AlignTop; brand: modelData.brand }
                ColumnLayout {
                    id: details; Layout.fillWidth: true; spacing: 5
                    Text { text: modelData.id; color: Tokens.text; font.pixelSize: 14 }
                    Text { Layout.fillWidth: true; text: modelData.role+" · "+modelData.quantization+" · "+(modelData.size/1073741824).toFixed(2)+" GiB"; color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; text: host.t(modelData.in_use ? "In use" : modelData.loaded ? "Loaded" : "Installed")+(modelData.selected ? " · "+host.t("Selected") : "")+" · "+modelData.capabilities.join(", "); color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 11 }
                    RowLayout {
                        SoftButton { text: host.t("Select"); enabled: modelData.chat && !client.modelChanging; onClicked: client.selectModel(modelData.id) }
                        SoftButton { text: host.t("Default"); enabled: modelData.chat; onClicked: client.feature("models.default",{model:modelData.id,conversation_id:client.selected}) }
                        SoftButton { text: host.t("Details"); onClicked: { modelDetails.row=modelData;modelDetails.open() } }
                        SoftButton { text: "×"; enabled: !modelData.in_use; onClicked: { remove.modelId=modelData.id;remove.roles=modelData.roles.join(", ");remove.open() } }
                    }
                }
            }
        }
    }
    SoftButton { visible: !!client.modelState.last_good && client.modelState.last_good!==client.currentModel; text: host.t("Return to last working model")+" · "+(client.modelState.last_good || ""); onClicked: client.selectModel(client.modelState.last_good) }
    Text { text: host.t("Download / import"); color: Tokens.text }
    TextField { id: name; Layout.fillWidth: true; placeholderText: "model:tag" }
    RowLayout {
        Text { text: host.t("Size limit (MiB)"); color: Tokens.secondary; font.pixelSize: 12 }
        SpinBox { id: limit; from: 32; to: 32768; value: 2048; editable: true }
    }
    RowLayout {
        SoftButton { text: host.t("Download"); onClicked: { install.kind="pull";install.open() } }
        SoftButton { text: host.t("Import GGUF"); onClicked: gguf.open() }
    }
    Repeater {
        model: client.modelState.jobs || []
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true
            Text { Layout.fillWidth: true; text: modelData.model+" · "+panel.host.t(modelData.state)+(modelData.error ? "\n"+panel.host.t("Action could not be completed") : ""); color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
            ProgressBar { Layout.fillWidth: true; from: 0; to: modelData.total || 1; value: modelData.completed; indeterminate: modelData.state==="running" && !modelData.total }
            RowLayout {
                SoftButton { visible: ["queued","running","cancelling"].indexOf(modelData.state)>=0; text: host.t("Cancel"); onClicked: client.feature("models.cancel",{job_id:modelData.id}) }
                SoftButton { visible: ["failed","cancelled","interrupted"].indexOf(modelData.state)>=0; text: host.t("Retry"); onClicked: client.feature("models.retry",{job_id:modelData.id}) }
            }
        }
    }
    Text { Layout.fillWidth: true; text: client.featureStatus==="Saved" ? "" : host.t(client.featureStatus); color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
    FileDialog { id: gguf; title: panel.host.t("Import GGUF"); nameFilters: ["GGUF (*.gguf)"]; onAccepted: { install.kind="import";client.inspectImport(selectedFile);install.open() } }
    Dialog {
        id: install; property string kind: "pull"; anchors.centerIn: Overlay.overlay; parent: Overlay.overlay
        width: Math.min(460,panel.host.width-40); modal: true; title: panel.host.t("Confirm installation")
        background: Rectangle { color: Tokens.panel; radius: 12; border.color: Tokens.border }
        contentItem: ColumnLayout {
            Text { visible: install.kind==="import"; Layout.fillWidth: true; text: client.importReview.architecture ? client.importReview.architecture+" · "+(client.importReview.bytes/1048576).toFixed(1)+" MiB · GGUF v"+client.importReview.version : client.featureStatus; color: Tokens.secondary; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: name.text+"\n"+(install.kind==="pull" ? "Ollama official registry · HTTPS\n"+panel.host.t("Exact size and format are unknown until the manifest arrives.") : gguf.selectedFile+"\nGGUF")+"\n"+panel.host.t("Size limit (MiB)")+": "+limit.value+"\n"+panel.host.t("Installation uses disk and network; it does not load weights or change your default. Memory reserve remains enforced."); color: Tokens.text; wrapMode: Text.Wrap }
            RowLayout {
                SoftButton { text: panel.host.t("Cancel"); onClicked: install.close() }
                SoftButton { text: panel.host.t("Install"); enabled: install.kind==="pull" || !!client.importReview.architecture; onClicked: { client.installModel(install.kind,name.text,gguf.selectedFile,limit.value);install.close() } }
            }
        }
    }
    Dialog {
        id: remove; property string modelId: ""; property string roles: ""; parent: Overlay.overlay; anchors.centerIn: parent; width: 420; modal: true; title: panel.host.t("Delete model")
        background: Rectangle { color: Tokens.panel; radius: 12; border.color: Tokens.border }
        contentItem: ColumnLayout {
            Text { Layout.fillWidth: true; text: remove.modelId+"\n"+remove.roles+"\n"+panel.host.t("Assigned or busy models are protected. Chats and original files remain."); color: Tokens.text; wrapMode: Text.Wrap }
            RowLayout {
                SoftButton { text: panel.host.t("Cancel"); onClicked: remove.close() }
                SoftButton { text: panel.host.t("Delete model"); onClicked: { client.feature("models.delete",{model:remove.modelId});remove.close() } }
            }
        }
    }
}
