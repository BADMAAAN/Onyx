import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Pane {
    id: settings
    required property var host
    function t(s) { return host.t(s) }
    property int page: 0
    property var categories: ["General","Appearance","Voice and Live","Memory","Internet","Keyboard shortcuts","Data and diagnostics"]
    // Index complete translation keys, including descriptions, without IO or model calls.
    property var keywords: [["Language", "Show welcome", "Welcome / new draft", "Last conversation", "X: hide to tray", "X: close client"], ["Text size", "Reduced motion", "Logo glow", "Reset appearance"], ["Dictation inserts editable text. Only Send executes a request.", "Read responses aloud", "Wake listening (restart)", "Retry connection", "Input device", "Unavailable", "Output device", "System default", "Current voice", "Audio and hotkey changes require a backend restart. Other app hotkey conflicts cannot be detected."], ["Forget removes the selected saved fact and search index. History, task journal and backups remain.", "Apply", "Forget memory", "No saved preferences", "Pause observation", "Retention (days, restart)", "Excluded apps (restart)"], ["Off", "Only on request", "When needed", "Answers use the local model. Search sends a short public query to DuckDuckGo.", "Clear web cache", "Computer actions use the existing Onyx permissions. This page does not grant full access."], ["Apply", "Reset", "Show app hotkey (restart)", "Audio and hotkey changes require a backend restart. Other app hotkey conflicts cannot be detected.", "New chat", "Settings", "Dictation", "Stop"], ["Open data folder", "Export chat", "Open logs", "Close"]]
    onPageChanged: client.expandSettings(["General","Appearance","Voice & Dictation","Memory","Internet","Keyboard shortcuts","Data"][page],true)
    function indexSearch() {
        const q=search.text.toLocaleLowerCase()
        for(let i=0;i<categories.length;i++) if(([categories[i]].concat(keywords[i])).some(k=>(t(k)+" "+k).toLocaleLowerCase().indexOf(q)>=0)) {page=i;break}
        Qt.callLater(revealMatch)
    }
    objectName: "settingsDialog"
    padding: 20
    function open() { host.section="Settings";client.loadSettings();client.refreshModels() }
    function close() { host.section="Chat" }
    background: Rectangle { color: Tokens.background; radius: 12; border.color: Tokens.border }
    function matches(label,words) { const q=search.text.toLocaleLowerCase();return !q || (t(label)+" "+label+" "+words).toLocaleLowerCase().indexOf(q)>=0 }
    function revealMatch() { scroll.contentItem.contentY=0 }
    contentItem: ColumnLayout {
        spacing: 12
        Text { text: settings.t("Settings"); color: Tokens.text; font.pixelSize: 24 }
        TextField { id: search; objectName: "settingsSearch"; Layout.fillWidth: true; placeholderText: t("Search settings"); onTextChanged: settings.indexSearch() }
        ScrollView { id: scroll; objectName: "settingsScroll"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            GridLayout { width: scroll.availableWidth-14; columns: width>1380 ? 3 : width>820 ? 2 : 1; columnSpacing: 12; rowSpacing: 12
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "General"; heading: settings.t("General"); summary: settings.t("Language, startup and shortcuts"); searching: !!search.text; visible: settings.matches("General","Language язык welcome приветствие startup поведение keyboard hotkey shortcut клавиши горячие сочетания")
                        Text { text: settings.t("Language"); color: Tokens.secondary }
                        SettingCombo { Layout.fillWidth: true; model: ["English","Русский"]; currentIndex: client.language==="ru" ? 1 : 0; onActivated: client.preference("language",currentIndex ? "ru" : "en") }
                        SettingCheck { text: settings.t("Show welcome"); checked: client.showWelcome; onToggled: client.preference("showWelcome",checked) }
                        SettingCombo { Layout.fillWidth: true; model: [settings.t("Welcome / new draft"),settings.t("Last conversation")]; currentIndex: client.startup==="last" ? 1 : 0; onActivated: client.preference("startup",currentIndex ? "last" : "welcome") }
                        SettingCombo { Layout.fillWidth: true; model: [settings.t("X: hide to tray"),settings.t("X: close client")]; currentIndex: client.closeBehavior==="quit" ? 1 : 0; onActivated: client.preference("closeBehavior",currentIndex ? "quit" : "hide") }
                        Repeater { model: [{key:"shortcutNew",label:"New chat"},{key:"shortcutSettings",label:"Settings"},{key:"shortcutDictation",label:"Dictation"},{key:"shortcutStop",label:"Stop"}]
                            delegate: ColumnLayout { required property var modelData; Layout.fillWidth: true
                                Text { text: settings.t(modelData.label); color: Tokens.text }
                                RowLayout {
                                    TextField { id: shortcutInput; Layout.fillWidth: true; text: client.shortcuts[modelData.key] }
                                    SoftButton { text: settings.t("Apply"); onClicked: client.preference(modelData.key,shortcutInput.text) }
                                    SoftButton { text: settings.t("Reset"); onClicked: client.resetShortcut(modelData.key) }
                                }
                            }
                        }
                        Text { text: settings.t("Show app hotkey (restart)"); color: Tokens.secondary }
                        RowLayout { TextField { id: showHotkey; Layout.fillWidth: true; text: client.backendSettings.values && client.backendSettings.values.show_hotkey || client.backendSettings.show_hotkey || ""; placeholderText: "ctrl+alt+o" } SoftButton { text: settings.t("Apply"); enabled: client.ready && !client.preview; onClicked: client.backendPreference("show_hotkey",showHotkey.text) } }
                        Text { Layout.fillWidth: true; text: settings.t("Audio and hotkey changes require a backend restart. Other app hotkey conflicts cannot be detected."); color: Tokens.secondary; wrapMode: Text.Wrap }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Appearance"; heading: settings.t("Appearance"); summary: settings.t("Colors, type and motion"); searching: !!search.text; visible: settings.matches("Appearance","theme тема colors цвета font шрифт motion движение")
                    Pearl { Layout.preferredWidth: 70; Layout.preferredHeight: 70; Layout.alignment: Qt.AlignHCenter; animate: settings.visible && parent.visible && !client.reducedMotion; glow: client.glow }
                        RowLayout { Text { text: settings.t("Text size"); color: Tokens.text; Layout.fillWidth: true } SpinBox { from: 12; to: 22; value: client.textSize; onValueModified: client.preference("textSize",value) } }
                        SettingCheck { text: settings.t("Reduced motion"); checked: client.reducedMotion; onToggled: client.preference("reducedMotion",checked) }
                        RowLayout { Text { text: settings.t("Logo glow"); color: Tokens.text } Slider { Layout.fillWidth: true; from: 0; to: 1; value: client.glow; onPressedChanged: if(!pressed) client.preference("glow",value) } }
                        SoftButton { text: settings.t("Reset appearance"); onClicked: client.resetGroup("appearance") }
                    Text { text: settings.t("Background / text / accent"); color: Tokens.secondary }
                    RowLayout {
                        TextField { id: bg; Layout.fillWidth: true; text: client.appearance.backgroundColor; maximumLength: 7 }
                        SoftButton { text: settings.t("Apply"); onClicked: client.preference("backgroundColor",bg.text) }
                    }
                    RowLayout {
                        TextField { id: fg; Layout.fillWidth: true; text: client.appearance.textColor; maximumLength: 7 }
                        SoftButton { text: settings.t("Apply"); onClicked: client.preference("textColor",fg.text) }
                    }
                    RowLayout {
                        TextField { id: ac; Layout.fillWidth: true; text: client.appearance.accentColor; maximumLength: 7 }
                        SoftButton { text: settings.t("Apply"); onClicked: client.preference("accentColor",ac.text) }
                    }
                    SettingCombo { Layout.fillWidth: true; model: Qt.fontFamilies(); currentIndex: model.indexOf(client.appearance.fontFamily); onActivated: client.preference("fontFamily",currentText) }
                    SettingCombo { Layout.fillWidth: true; model: [settings.t("Compact"),settings.t("Comfortable")]; currentIndex: client.appearance.density==="comfortable" ? 1 : 0; onActivated: client.preference("density",currentIndex ? "comfortable" : "compact") }
                    SettingCombo { Layout.fillWidth: true; model: [settings.t("Black"),settings.t("Cosmic")]; currentIndex: client.appearance.backgroundPreset==="cosmic" ? 1 : 0; onActivated: client.preference("backgroundPreset",currentIndex ? "cosmic" : "black") }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Models"; heading: settings.t("Models"); summary: client.currentModel || settings.t("Select a model"); searching: !!search.text; visible: settings.matches("Models","модели model download import загрузка импорт")
                    ModelsPanel { Layout.fillWidth: true; host: settings.host }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Voice & Dictation"; heading: settings.t("Voice & Dictation"); summary: settings.t("Microphone, transcription and voice"); searching: !!search.text; visible: settings.matches("Voice & Dictation","микрофон microphone input device output вход выход wake пробуждение dictation диктовка voice голос current voice")
                        Text { Layout.fillWidth: true; text: settings.t("Dictation inserts editable text. Only Send executes a request."); color: Tokens.secondary; wrapMode: Text.Wrap }
                        SettingCheck { text: settings.t("Read responses aloud"); checked: client.spoken; onToggled: client.preference("spoken",checked) }
                        SettingCheck { text: settings.t("Wake listening (restart)"); checked: client.backendSettings.values && client.backendSettings.values.wake_enabled !== undefined ? client.backendSettings.values.wake_enabled : !!client.backendSettings.wake_enabled; enabled: !!client.backendSettings.values; onToggled: client.backendPreference("wake_enabled",checked) }
                        Text { Layout.fillWidth: true; text: settings.t(client.voiceState); color: Tokens.secondary; wrapMode: Text.Wrap }
                        SoftButton { text: settings.t("Retry connection"); onClicked: client.retry() }
                        Text { text: settings.t("Input device"); color: Tokens.secondary }
                        SettingCombo { Layout.fillWidth: true; model: client.backendSettings.inputs || []; textRole: "label"; currentIndex: -1; displayText: client.backendSettings.values && client.backendSettings.values.microphone || client.backendSettings.configured_input || settings.t("Unavailable"); onActivated: client.backendPreference("microphone",model[currentIndex].identity) }
                        Text { text: settings.t("Output device"); color: Tokens.secondary }
                        SettingCombo { Layout.fillWidth: true; model: client.backendSettings.outputs || []; textRole: "label"; currentIndex: -1; displayText: client.backendSettings.values && client.backendSettings.values.output_device || client.backendSettings.configured_output || settings.t("System default"); onActivated: client.backendPreference("output_device",model[currentIndex].identity) }
                        Text { text: settings.t("Current voice")+": "+(client.backendSettings.voice || "—"); color: Tokens.secondary }
                        Text { Layout.fillWidth: true; text: settings.t("Audio and hotkey changes require a backend restart. Other app hotkey conflicts cannot be detected."); color: Tokens.secondary; wrapMode: Text.Wrap }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Live"; heading: "Onyx Live"; summary: settings.t("A voice conversation in this chat"); searching: !!search.text; visible: settings.matches("Onyx Live","лайв голос палитра palette")
                    Pearl { Layout.preferredWidth: 88; Layout.preferredHeight: 88; Layout.alignment: Qt.AlignHCenter; animate: settings.visible && parent.visible && !client.reducedMotion; glow: client.glow }
                    SettingCombo { Layout.fillWidth: true; model: [settings.t("Pearl"),settings.t("Cool"),settings.t("Lilac"),settings.t("Custom")]; currentIndex: ["pearl","cool","lilac","custom"].indexOf(client.appearance.livePalette); onActivated: client.preference("livePalette",["pearl","cool","lilac","custom"][currentIndex]) }
                    RowLayout { Text { text: settings.t("Logo size"); color: Tokens.secondary } Slider { Layout.fillWidth: true; from: 72; to: 120; value: client.appearance.liveSize; onMoved: client.preference("liveSize",value) } }
                    RowLayout { Text { text: settings.t("Calm motion (seconds)"); color: Tokens.secondary } Slider { Layout.fillWidth: true; from: 5; to: 30; value: client.appearance.liveSpeed; onMoved: client.preference("liveSpeed",value) } }
                    Text { Layout.fillWidth: true; text: settings.t(client.voiceState); color: Tokens.secondary; wrapMode: Text.Wrap }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Internet"; heading: settings.t("Internet"); summary: settings.t("Local mode and web access"); searching: !!search.text; visible: settings.matches("Internet","internet сеть интернет локально поиск search web DuckDuckGo")
                        SettingCombo { Layout.fillWidth: true; model: [settings.t("Only local"),settings.t("Local + Internet")]; enabled: !!client.backendSettings.values; currentIndex: client.backendSettings.values && client.backendSettings.values.internet!=="off" ? 1 : 0; onActivated: client.backendPreference("internet",["off","explicit"][currentIndex]) }
                        Text { Layout.fillWidth: true; text: settings.t("Answers use the local model. Search sends a short public query to DuckDuckGo."); color: Tokens.secondary; wrapMode: Text.Wrap }
                        SoftButton { text: settings.t("Clear web cache"); enabled: client.ready && !client.preview; onClicked: client.clearWebCache() }
                        Text { Layout.fillWidth: true; text: settings.t("Computer actions use the existing Onyx permissions. This page does not grant full access."); color: Tokens.secondary; wrapMode: Text.Wrap }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Git"; heading: "Git & GitHub"; summary: settings.t("Local commit / separate push"); searching: !!search.text; visible: settings.matches("Git & GitHub","проект коммит публикация project commit push")
                    GitPanel { Layout.fillWidth: true; host: settings.host }
                }
                SettingSection { Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.preferredWidth: (scroll.availableWidth-40)/3; sectionKey: "Data"; heading: settings.t("Data"); summary: settings.t("History, memory and backups"); searching: !!search.text; visible: settings.matches("Data","data diagnostics данные история backup резервная копия logs логи memory память retention хранение exclusions исключения")
                        Text { Layout.fillWidth: true; text: "Onyx · 0.5.0"; color: Tokens.text; font.pixelSize: 18 }
                        Text { Layout.fillWidth: true; text: settings.t("Your local assistant. Conversations, memory and models stay on this computer."); color: Tokens.secondary; wrapMode: Text.Wrap }
                        SoftButton { text: settings.t("Licenses"); onClicked: client.openLicenses() }
                        SoftButton { text: settings.t("Diagnostics"); onClicked: diagnosticsDialog.open() }
                        SoftButton { text: settings.t("Open data folder"); onClicked: client.openData() }
                        SoftButton { text: settings.t("Export chat"); enabled: !client.welcome; onClicked: client.exportChat() }
                        SoftButton { text: settings.t("Open logs"); onClicked: client.openLogs() }
                        Text { Layout.fillWidth: true; text: settings.t("Forget removes the selected saved fact and search index. History, task journal and backups remain."); color: Tokens.secondary; wrapMode: Text.Wrap }
                        Repeater { model: client.savedMemories
                            delegate: ColumnLayout { required property var modelData; Layout.fillWidth: true
                                Text { text: modelData.key; color: Tokens.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                TextField { id: memoryValue; text: modelData.value; Layout.fillWidth: true; selectByMouse: true }
                                RowLayout {
                                    SoftButton { text: settings.t("Apply"); onClicked: client.saveMemory(modelData.key,memoryValue.text) }
                                    SoftButton { text: settings.t("Forget memory"); onClicked: client.forgetMemory(modelData.key) }
                                }
                            }
                        }
                        Text { visible: client.savedMemories.length===0; text: settings.t("No saved preferences"); color: Tokens.secondary }
                        SettingCheck { text: settings.t("Pause observation"); checked: !!client.backendSettings.observation_paused; enabled: !!client.backendSettings.values; onToggled: client.backendPreference("observation_paused",checked) }
                        RowLayout { Text { text: settings.t("Retention (days, restart)"); color: Tokens.secondary } SpinBox { from: 1; to: 30; value: client.backendSettings.retention_days || 7; enabled: !!client.backendSettings.values; onValueModified: client.backendPreference("retention_days",value) } }
                        Text { text: settings.t("Excluded apps (restart)"); color: Tokens.secondary }
                        RowLayout { TextField { id: exclusions; Layout.fillWidth: true; text: (client.backendSettings.excluded_apps || []).join(", "); placeholderText: "example.exe" } SoftButton { text: settings.t("Apply"); enabled: !!client.backendSettings.values; onClicked: client.backendPreference("excluded_apps",exclusions.text) } }
                }
            }
        }
        RowLayout { Layout.fillWidth: true
            Text { Layout.fillWidth: true; text: client.settingsStatus === "Saved" ? "" : settings.t(client.settingsStatus); color: Tokens.secondary; wrapMode: Text.Wrap }
            SoftButton { text: settings.t("Close"); onClicked: settings.close() }
        }
    }
    Dialog {
        id: diagnosticsDialog; title: settings.t("Diagnostics"); modal: true
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(620,parent.width-40); height: Math.min(430,parent.height-40)
        standardButtons: Dialog.Close
        background: Rectangle { color: Tokens.panel; border.color: Tokens.border; radius: 12 }
        contentItem: ScrollView {
            clip: true
            TextArea { text: client.diagnostics+"\n\n"+client.error; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText; color: Tokens.secondary; font.pixelSize: 12; background: null }
        }
    }
}
