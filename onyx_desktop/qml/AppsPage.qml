import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
ScrollView {
    id: page
    required property var host
    clip: true
    ColumnLayout {
        width: page.availableWidth-24; spacing: 12
        Text { text: host.t("Apps"); color: Tokens.text; font.pixelSize: 24 }
        Text { Layout.fillWidth: true; text: host.t("Applications and integrations. External coding services use their own accounts and permissions."); color: Tokens.secondary; wrapMode: Text.Wrap }
        GridLayout {
            Layout.fillWidth: true; columns: page.width>1160 ? 3 : page.width>700 ? 2 : 1; columnSpacing: 10; rowSpacing: 10
            Repeater {
                model: client.apps
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true; implicitHeight: card.implicitHeight+16; color: Tokens.panel; border.color: Tokens.border; radius: 12
                    ColumnLayout {
                        id: card; x: 14; y: 8; width: parent.width-28; spacing: 4
                        RowLayout {
                            Image { property string asset: ({"claude":"anthropic.png","codex":"openai.png","droid":"factory-qt.svg","qwen-code":"qwen.png","opencode":"opencode.png","hermes":"hermes.png","hermes-desktop":"hermes.png","cline":"cline.png","oh-my-pi":"oh-my-pi.png","deepseek-harness":"deepseek-harness.png","openclaw":"openclaw.svg","pi":"pi.svg","copilot":"copilot.svg"}[modelData.id] || "")
                                source: asset ? "../assets/"+asset : "../assets/icons/terminal.svg"
                                Layout.preferredWidth: 24; Layout.preferredHeight: 24; fillMode: Image.PreserveAspectFit
                                HoverHandler { id: iconHover }
                                GraphiteToolTip { visible: iconHover.hovered; text: parent.asset ? page.host.t("Publisher / product mark") : page.host.t("Neutral application icon") }
                            }
                            Text { text: modelData.id==="copilot" ? "GitHub Copilot CLI" : modelData.name; color: Tokens.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                        }
                        Text { Layout.fillWidth: true; text: modelData.publisher+" · "+page.host.t(modelData.connected ? "Connected" : modelData.installed ? "Installed · not connected" : "Not installed"); color: Tokens.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }

                        RowLayout {
                            SoftButton { text: page.host.t("Open"); visible: modelData.launchable; onClicked: client.feature("apps.open",{id:modelData.id}) }
                            SoftButton { text: page.host.t("Documentation"); enabled: client.internetMode!=="off"; onClicked: client.openSource(modelData.docs) }
                        }
                    }
                }
            }
        }
        Text { Layout.fillWidth: true; text: client.featureStatus === "Saved" || !client.featureStatus ? "" : page.host.t(client.featureStatus === "Working…" ? "Refreshing…" : "Action could not be completed"); color: Tokens.secondary; wrapMode: Text.Wrap }
    }
}
