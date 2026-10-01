import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
ColumnLayout {
    id: panel
    required property var host
    property var selectedFiles: []
    RowLayout {
        SoftButton { text: host.t("Choose project"); onClicked: folder.open() }
        SoftButton { text: host.t("Refresh"); onClicked: client.feature("git.status",{}) }
    }
    Text { Layout.fillWidth: true; text: (client.gitState.root || host.t("No project selected"))+"\n"+(client.gitState.branch || "")+" · "+(client.gitState.auth || ""); color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
    Repeater {
        model: client.gitState.files || []
        delegate: SettingCheck {
            required property var modelData
            Layout.fillWidth: true; text: modelData.status+"  "+modelData.path
            checked: panel.selectedFiles.indexOf(modelData.path)>=0
            onToggled: { const files=panel.selectedFiles.filter(p=>p!==modelData.path);if(checked)files.push(modelData.path);panel.selectedFiles=files }
        }
    }
    TextField { id: message; Layout.fillWidth: true; placeholderText: host.t("Commit message") }
    SoftButton { text: host.t("Review selected files"); enabled: panel.selectedFiles.length>0 && !!message.text; onClicked: client.feature("git.preview",{paths:panel.selectedFiles,message:message.text}) }
    RowLayout {
        SettingCombo { id: remote; Layout.fillWidth: true; model: client.gitState.remotes || []; textRole: "name" }
        SoftButton { text: host.t("Review push"); enabled: client.internetMode!=="off" && remote.currentIndex>=0; onClicked: client.feature("git.pushPreview",{remote:remote.model[remote.currentIndex].name}) }
    }
    Text { Layout.fillWidth: true; text: host.t("Commit stays local. Push publishes only after a separate review. Authentication is configured in your system terminal; never paste tokens here."); color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
    Text { Layout.fillWidth: true; text: client.featureStatus; color: Tokens.secondary; wrapMode: Text.Wrap; font.pixelSize: 12 }
    FolderDialog { id: folder; onAccepted: { panel.selectedFiles=[];client.chooseProject(selectedFolder) } }
    Dialog {
        id: review; parent: Overlay.overlay; anchors.centerIn: parent; width: Math.min(720,panel.host.width-50); height: Math.min(600,panel.host.height-70); modal: true
        title: panel.host.t(client.gitReview.kind==="push" ? "Confirm push" : "Confirm local commit")
        visible: !!client.gitReview.receipt
        background: Rectangle { color: Tokens.panel; radius: 12; border.color: Tokens.border }
        contentItem: ColumnLayout {
            Text { Layout.fillWidth: true; text: (client.gitReview.root || "")+"\n"+(client.gitReview.branch || "")+" · "+(client.gitReview.remote || "")+"\n"+(client.gitReview.paths || []).join("\n"); color: Tokens.text; wrapMode: Text.Wrap }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea { text: client.gitReview.diff || client.gitReview.outgoing || ""; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText; font.family: "Consolas"; font.pixelSize: 12 }
            }
            RowLayout {
                SoftButton { text: panel.host.t("Cancel"); onClicked: { client.dismissReview();review.close() } }
                SoftButton { text: panel.host.t(client.gitReview.kind==="push" ? "Push" : "Commit"); enabled: !client.gitReview.truncated; onClicked: client.feature(client.gitReview.kind==="push" ? "git.push" : "git.commit",{receipt:client.gitReview.receipt}) }
            }
        }
    }
}
