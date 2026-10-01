import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Popup {
    id: picker
    objectName: "modelPicker"
    required property var host
    signal manage()
    width: 292; height: Math.min(340,host.height-110,124+client.models.filter(m=>m.chat).length*46); padding: 12
    background: Rectangle { color: Tokens.panel; radius: 12; border.color: Tokens.border }
    onOpened: client.refreshModels()
    contentItem: ColumnLayout {
        TextField { id: search; Layout.fillWidth: true; placeholderText: host.t("Search installed models") }
        ListView {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
            model: client.models.filter(m=>m.chat && (!search.text || m.id.toLowerCase().indexOf(search.text.toLowerCase())>=0))
            delegate: Button {
                required property var modelData
                width: ListView.view.width; height: 42
                background: Rectangle { radius: 7; color: client.currentModel===modelData.id ? Tokens.hover : parent.hovered ? Tokens.elevated : "transparent" }
                contentItem: RowLayout {
                    ModelIdentity { brand: modelData.brand; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                    ColumnLayout {
                        Text { Layout.fillWidth: true; text: modelData.id; color: Tokens.text; font.pixelSize: 13; wrapMode: Text.Wrap }
                        Text { text: host.t(modelData.in_use ? "In use" : modelData.loaded ? "Loaded" : "Installed"); color: Tokens.secondary; font.pixelSize: 11 }
                    }
                    Text { text: client.currentModel===modelData.id ? "✓" : ""; color: Tokens.accent }
                }
                onClicked: { client.selectModel(modelData.id); picker.close() }
            }
        }
        Text { Layout.fillWidth: true; visible: !!client.modelState.error; text: host.t("Models are temporarily unavailable"); color: Tokens.secondary; wrapMode: Text.Wrap }
        SoftButton { Layout.fillWidth: true; text: host.t("Manage / add models"); onClicked: { picker.close(); picker.manage() } }
    }
}
