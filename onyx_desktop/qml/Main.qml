import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Dialogs

ApplicationWindow {
    id: win
    width: Tokens.controlWidth; height: Tokens.controlHeight
    minimumWidth: 760; minimumHeight: 600
    visible: true; title: "Onyx"; color: Tokens.background
    flags: Qt.Window
    font.family: Tokens.font; font.pixelSize: client.textSize
    palette.window: Tokens.panel; palette.base: Tokens.elevated; palette.button: Tokens.elevated
    palette.text: Tokens.text; palette.windowText: Tokens.text; palette.buttonText: Tokens.text
    palette.highlight: Tokens.hover; palette.highlightedText: Tokens.text; palette.mid: Tokens.border
    property bool forceQuit: false
    Timer { id: geometrySave; interval: 600; onTriggered: if(win.visible && win.visibility === Window.Windowed) client.saveGeometry(win.x,win.y,win.width,win.height) }
    onXChanged: geometrySave.restart()
    onYChanged: geometrySave.restart()
    onWidthChanged: geometrySave.restart()
    onHeightChanged: geometrySave.restart()
    onClosing: function(close) { if (!forceQuit && client.closeBehavior === "hide") { close.accepted=false; win.hide() } }
    property bool liveOpen: false
    property bool sidebarOpen: client.appearance.sidebarOpen !== false
    property string section: "Chat"
    Binding { target: Tokens; property: "background"; value: client.appearance.backgroundColor || "#000000" }
    Binding { target: Tokens; property: "text"; value: client.appearance.textColor || "#EEEEEE" }
    Binding { target: Tokens; property: "accent"; value: client.appearance.accentColor || "#B8A4FF" }
    Binding { target: Tokens; property: "font"; value: client.appearance.fontFamily || "Segoe UI" }
    Binding { target: Tokens; property: "compact"; value: client.appearance.density !== "comfortable" }
    property var menuTarget: ({})
    function openChatMenu(chat) { menuTarget=chat; chatMenu.popup() }
    property bool acceptanceMotion: false
    property bool motion: (!client.preview || acceptanceMotion) && !client.reducedMotion && visible && visibility !== Window.Minimized
    property var lastEditor: composer
    onActiveFocusItemChanged: { if(activeFocusItem && activeFocusItem.selectAll) lastEditor=activeFocusItem }
    property bool following: true
    property bool newMessages: false
    property int localHour: client.preview ? 18 : new Date().getHours()
    function t(s) { let lang = client.language; return client.tr(s) }
    function bottom() { messages.positionViewAtEnd(); following = true; newMessages = false }
    function explain(s) { notice.text = win.t(s); notice.open() }
    function openLive() { liveOpen=true;composer.focus=false;center.forceActiveFocus();if(!client.liveActive) client.startLive() }
    function closeLive() { client.stopLive();liveOpen=false }
    Timer { interval: 60000; running: !client.preview && win.visible && win.visibility !== Window.Minimized; repeat: true; onTriggered: win.localHour = new Date().getHours() }
    Connections {
        target: client
        function onDraftChanged() { if(composer.text !== client.draft) composer.text = client.draft }
        function onInsertTranscript(position,text) { composer.insert(position,text) }
        function onContentChanged() { if (win.following) Qt.callLater(win.bottom); else win.newMessages = true }
        function onLiveStarted() { win.liveOpen=true }
        function onLiveBackgroundStarted() { win.hide() }
        function onSelectionChanged() { if(win.liveOpen || client.liveActive) win.closeLive() }
    }
    Action { id: newChatAction; text: win.t("New chat"); shortcut: client.shortcuts.shortcutNew; onTriggered: { win.section="Chat";client.newChat() } }
    Action { id: settingsAction; text: win.t("Settings"); shortcut: client.shortcuts.shortcutSettings; onTriggered: settings.open() }
    Action { id: attachAction; text: win.t("Choose a text file…"); onTriggered: filePicker.open() }
    Action { id: sendAction; text: win.t(client.busy ? "Stop" : "Send"); onTriggered: client.busy ? client.cancel() : client.send() }
    Action { id: dictationAction; text: win.t("Dictation"); shortcut: client.shortcuts.shortcutDictation; onTriggered: client.dictationState === "recording" ? client.finishDictation() : client.startDictation(composer.cursorPosition) }
    Action { id: stopAction; text: win.t("Stop"); shortcut: client.shortcuts.shortcutStop; onTriggered: client.dictationBusy ? client.cancelDictation() : client.cancel() }
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: Tokens.outer; anchors.rightMargin: Tokens.outer-3
        anchors.topMargin: Tokens.contentTop; anchors.bottomMargin: Tokens.outer+1
        spacing: Tokens.gap
        Rectangle {
            id: sidebar
            visible: win.sidebarOpen
            Layout.fillHeight: true; Layout.preferredWidth: Tokens.sidebarWidth
            color: Tokens.sidebar; radius: 10
            Row { x: 14; y: 12; spacing: 8
                Pearl { width: 28; height: 28 }
                Text { text: "Onyx"; color: Tokens.text; font.pixelSize: 19; anchors.verticalCenter: parent.verticalCenter }
            }
            Column { x: 8; y: 52; width: parent.width-16; spacing: 2
                Repeater { model: ["Apps","Chat","Settings"]
                    delegate: SoftButton { required property string modelData; width: parent.width; height: 33; text: win.t(modelData)
                        background: Rectangle { radius: 7; color: win.section===parent.modelData ? Tokens.elevated : parent.hovered ? Tokens.hover : "transparent" }
                        contentItem: Row { spacing: 12; leftPadding: 12
                            Image { width: 17; height: 17; anchors.verticalCenter: parent.verticalCenter; source: "../assets/icons/"+modelData.toLowerCase()+".svg" }
                            Text { text: win.t(modelData); color: Tokens.text; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                        }
                        onClicked: { if(modelData==="Settings") settings.open(); else { win.section=modelData;if(modelData==="Apps")client.feature("apps.list",{}); } }
                    }
                }
            }
            SoftButton { x: 10; y: 164; width: parent.width-20; height: 32; text: "+  "+win.t("New chat"); onClicked: newChatAction.trigger() }
            TextField { id: chatSearch; x: 10; y: 206; width: parent.width-20; height: 31; placeholderText: win.t("Search chats"); font.pixelSize: 12; color: Tokens.text; selectByMouse: true; background: Rectangle { radius: 7; color: Tokens.elevated } }
            ChatSidebar {
                x: 8; y: 250; width: parent.width-16; height: parent.height-y-8
                host: win; query: chatSearch.text
                onEditProject: function(project) { projectDialog.projectId=project.project_id;projectTitle.text=project.title;projectPath.text=project.workspace_path || "";projectDialog.open() }
                onCreateProject: { projectDialog.projectId="";projectTitle.text="";projectPath.text="";projectDialog.open() }
            }
        }
        Rectangle {
            id: center; visible: win.section==="Chat"; Layout.fillWidth: true; Layout.fillHeight: true
            color: Tokens.background; radius: 10; clip: true
            Image { anchors.fill: parent; visible: client.appearance.backgroundPreset==="cosmic"; source: "../assets/center-lower.png"; fillMode: Image.PreserveAspectCrop; opacity: .35 }
            Flickable {
                id: welcome; anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: composerPanel.top; anchors.bottomMargin: 12
                contentWidth: width; contentHeight: height; clip: true; interactive: contentHeight>height
                ScrollBar.vertical: ScrollBar { }
                property bool introReady: false
                Component.onCompleted: introReady=true
                visible: opacity > 0; opacity: client.welcome && client.showWelcome && introReady && !win.liveOpen ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: client.reducedMotion ? 0 : Tokens.transition } }
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: Math.max(36, (welcome.height-height)/2-16); spacing: 12
                    width: parent.width-80
                    Pearl { width: 144; height: 144; anchors.horizontalCenter: parent.horizontalCenter; animate: win.motion; glow: client.glow; envelope: (client.dictationState === "recording" || client.liveActive) && !client.reducedMotion ? client.voiceEnvelope : 0 }
                    Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: win.t(win.localHour < 12 ? "Good morning" : win.localHour < 18 ? "Good afternoon" : "Good evening"); color: Tokens.text; font.pixelSize: 24; wrapMode: Text.Wrap }
                    Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: win.t("How can I help?"); color: Tokens.secondary; font.pixelSize: 15 }
                    Flow {
                        width: Math.min(parent.width,540); anchors.horizontalCenter: parent.horizontalCenter; spacing: 8
                        Repeater { model: [{title:"Explain",prompt:"Explain a topic step by step: "},{title:"Write code",prompt:"Help me write code for "},{title:"Explore an idea",prompt:"Help me think through this idea: "}]
                            delegate: SoftButton { required property var modelData; width: (parent.width-16)/3; height: 46; text: win.t(modelData.title)
                                background: Rectangle { color: parent.hovered ? Tokens.hover : Tokens.panel; border.color: Tokens.border; radius: 9 }
                                onClicked: { composer.text=win.t(modelData.prompt);composer.forceActiveFocus();composer.cursorPosition=composer.length }
                            }
                        }
                    }
                }
            }

            RowLayout {
                x: 26; y: 8; width: parent.width-52
                Text { text: client.welcome ? "" : client.title; color: Tokens.muted; elide: Text.ElideRight; Layout.fillWidth: true }
                SoftButton { objectName: "liveMic"; visible: client.liveActive && !win.liveOpen; text: "Onyx Live"; height: 30; onClicked: win.liveOpen=true }
                IconButton { iconName: "more"; visible: !client.welcome; label: win.t("Conversation options"); width: 36; height: 36; onClicked: win.openChatMenu(client.chats.find(c => c.conversation_id === client.selected) || {}) }
            }
            Item {
                id: stage; anchors.top: parent.top; anchors.topMargin: 55; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: draftChips.top; anchors.bottomMargin: 12
                ListView {
                    id: messages
                    anchors.fill: parent; anchors.leftMargin: Math.max(Tokens.gutter,(parent.width-820)/2); anchors.rightMargin: Math.max(Tokens.gutter,(parent.width-820)/2)
                    clip: true; spacing: 16; cacheBuffer: 180
                    model: client.messageModel
                    visible: !client.welcome || count > 0
                    topMargin: client.welcome ? Math.max(0, height - 140) : 20; bottomMargin: 25
                    ScrollBar.vertical: ScrollBar { }
                    onMovementStarted: win.following = false
                    onMovementEnded: if (atYEnd) { win.following = true; win.newMessages = false }
                    header: SoftButton { text: win.t("Load earlier messages"); visible: client.hasMore; height: visible ? 40 : 0; onClicked: { win.following = false; client.older() } }
                    delegate: Item {
                        id: bubble
                        required property var sources; required property var provenance; required property var model_identity;
                        required property string role
                        required property string content
                        required property string status
                        required property var blocks
                        width: messages.width
                        height: body.implicitHeight + 30
                        Rectangle {
                            anchors.fill: parent; anchors.leftMargin: bubble.role === "user" ? Math.min(80, parent.width*.12) : 0
                            color: bubble.role === "user" ? "#1f1f1f" : "transparent"; radius: Tokens.radius
                        }
                        Column {
                            id: body
                            x: bubble.role === "user" ? Math.min(80, bubble.width*.12) + 20 : 8
                            y: 15; width: bubble.width - x - 20; spacing: 10
                            Text { text: bubble.role === "user" ? (client.language === "ru" ? "Вы" : "You") : "Onyx"+(bubble.model_identity && bubble.model_identity.actual_model ? " · "+bubble.model_identity.actual_model : ""); color: Tokens.muted; font.pixelSize: 11; font.weight: Font.DemiBold }
                            Repeater {
                                model: bubble.blocks
                                delegate: Column {
                                    id: block
                                    required property var modelData
                                    width: body.width; spacing: 4
                                    Rectangle {
                                        width: parent.width; height: 36; radius: 7; color: "#1f1f1f"; visible: block.modelData.code
                                        Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter; text: block.modelData.language || "code"; color: Tokens.muted; font.pixelSize: 11 }
                                        SoftButton { anchors.right: parent.right; height: 36; text: win.t("Copy code"); onClicked: client.copy(block.modelData.text) }
                                    }
                                    Flickable {
                                        width: parent.width; height: textBody.implicitHeight
                                        contentWidth: block.modelData.code ? Math.max(width, textBody.implicitWidth) : width
                                        contentHeight: height; clip: true; interactive: block.modelData.code
                                        ScrollBar.horizontal: ScrollBar { policy: block.modelData.code ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                                        TextEdit {
                                            id: textBody
                                            width: block.modelData.code ? Math.max(block.width, implicitWidth) : block.width
                                            text: block.modelData.text; textFormat: TextEdit.PlainText
                                            readOnly: true; selectByMouse: true; activeFocusOnTab: true
                                            wrapMode: block.modelData.code ? TextEdit.NoWrap : TextEdit.Wrap
                                            color: Tokens.text; selectionColor: "#333333"
                                            font.family: block.modelData.code ? "Consolas" : Tokens.font
                                            font.pixelSize: client.textSize
                                        }
                                    }
                                }
                            }
                            Column {
                                visible: bubble.role === "assistant"; width: parent.width; spacing: 4
                                Text { visible: bubble.provenance === "failed" || bubble.provenance === "searching" || !!(bubble.sources && bubble.sources.length>0); color: Tokens.secondary; font.pixelSize: 12; text: win.t(bubble.provenance === "failed" ? "Could not verify" : bubble.provenance === "searching" ? "Searching…" : "Search") + (bubble.sources && bubble.sources.length ? " · "+bubble.sources.length : "") }
                                Repeater { model: bubble.sources || []
                                    delegate: SoftButton { required property var modelData; width: parent.width; text: modelData.id+" · "+modelData.title+" · "+modelData.extraction; onClicked: client.openSource(modelData.url) }
                                }
                            }
                            Row {
                                spacing: 10
                                Text { visible: bubble.status === "interrupted" || bubble.status === "failed"; text: bubble.status === "interrupted" ? win.t("Interrupted — completed actions may remain.") : win.t("Action could not be completed"); color: Tokens.muted; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                                SoftButton { visible: bubble.role === "assistant" && bubble.content.length > 0; text: win.t("Copy"); height: 30; onClicked: client.copy(bubble.content) }
                            }
                        }
                    }
                }
                SoftButton { anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; text: "↓  " + win.t("New messages"); visible: win.newMessages; onClicked: win.bottom(); background: Rectangle { color: Tokens.elevated; radius: 15; border.color: Tokens.border } }

            }
            Row {
                id: draftChips; x: 24; anchors.bottom: composerPanel.top; anchors.bottomMargin: 8; spacing: 6; height: client.attachments.length ? 32 : 0
                Repeater { model: client.attachments
                    delegate: SoftButton { required property var modelData; required property int index; height: 32; text: modelData.name+"  ×"; onClicked: client.removeAttachment(index); GraphiteToolTip { text: win.t("Remove attachment"); visible: parent.hovered && win.visible } }
                }
            }
            SoftButton { anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: draftChips.top; anchors.bottomMargin: 4; visible: client.ready && client.error.length>0; text: win.t("Action could not be completed")+" · "+win.t("Details"); onClicked: {client.expandSettings("Data",true);settings.open()} }
            Rectangle {
                id: composerPanel; anchors.horizontalCenter: parent.horizontalCenter; width: Math.min(800,parent.width-40); height: win.liveOpen ? 210 : Math.min(190,Math.max(Tokens.compact ? 60 : 68,composer.contentHeight+22)); anchors.bottom: parent.bottom; anchors.bottomMargin: 28
                Behavior on height { NumberAnimation { duration: client.reducedMotion ? 0 : 180; easing.type: Easing.InOutQuad } }
                radius: 16; border.color: composer.activeFocus ? "#555555" : Tokens.border; color: Tokens.elevated
                IconButton { id: attachButton; visible: !win.liveOpen; x: 8; anchors.bottom: parent.bottom; anchors.bottomMargin: 12; width: 36; height: 36; iconName: "plus"; iconSize: 20; label: win.t("Choose a text file…"); onClicked: attachmentMenu.popup() }
                ScrollView {
                    visible: !win.liveOpen
                    x: 53; width: parent.width-155-(parent.width>620 ? 148 : 100); anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: 7; anchors.bottomMargin: 7; clip: true
                    TextArea {
                        id: composer; objectName: "composer"; color: Tokens.text; placeholderText: win.t("Message…"); placeholderTextColor: Tokens.muted
                        font.pixelSize: client.textSize; wrapMode: TextEdit.Wrap; selectByMouse: true; background: null; activeFocusOnTab: true
                        topPadding: 12; bottomPadding: 8; leftPadding: 0; rightPadding: 0
                        Accessible.name: win.t("Message…")
                        onTextChanged: client.setDraft(text)
                        Keys.onReturnPressed: function(event) { if (!(event.modifiers & Qt.ShiftModifier) && !inputMethodComposing) { if(!client.dictationBusy) sendAction.trigger(); event.accepted = true } else event.accepted = false }
                        Keys.onEnterPressed: function(event) { if (!(event.modifiers & Qt.ShiftModifier) && !inputMethodComposing) { if(!client.dictationBusy) sendAction.trigger(); event.accepted = true } else event.accepted = false }
                    }
                }
                SoftButton {
                    id: modelButton; objectName: "modelSelector"; anchors.right: dictationButton.left; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter
                    visible: !win.liveOpen
                    width: parent.width>620 ? 144 : 96; height: 34; text: client.currentModel || win.t("Models")
                    contentItem: RowLayout { spacing: 5
                        ModelIdentity { brand: (client.models.find(m=>m.id===client.currentModel) || {}).brand || "custom"; Layout.preferredWidth: 17; Layout.preferredHeight: 17 }
                        Text { Layout.fillWidth: true; text: client.modelChanging ? win.t("Selecting…") : modelButton.text; color: Tokens.text; elide: Text.ElideMiddle; font.pixelSize: 12 }
                        Text { text: "⌄"; color: Tokens.secondary }
                    }
                    enabled: client.ready && !client.modelChanging
                    onClicked: modelPicker.open()
                    ModelPicker { id: modelPicker; host: win; x: Math.min(0,modelButton.width-width); y: -height-10; onManage: { client.expandSettings("Models",true);settings.open() } }
                }
                IconButton {
                    id: dictationButton; objectName: "composerMic"; anchors.right: sendButton.left; anchors.rightMargin: 4; anchors.bottom: parent.bottom; anchors.bottomMargin: 10; width: 36; height: 36
                    visible: !win.liveOpen
                    iconName: client.dictationState === "recording" ? "stop" : "mic"; iconSize: 19
                    label: win.t(client.dictationState === "recording" ? "Finish recording" : "Dictation")
                    enabled: client.ready && !client.liveActive && (client.dictationState === "recording" || !client.dictationBusy)
                    onClicked: dictationAction.trigger()
                }
                IconButton {
                    id: sendButton; objectName: "sendButton"; anchors.right: parent.right; anchors.rightMargin: 8; anchors.bottom: parent.bottom; anchors.bottomMargin: 10; width: 36; height: 36
                    visible: !win.liveOpen
                    iconName: client.busy ? "stop" : "send"; iconSize: 20; label: win.t(client.busy ? "Stop" : composer.text.trim().length ? "Send" : "Start Onyx Live")
                    enabled: client.busy || (client.ready && !client.modelChanging && !client.dictationBusy && (composer.text.trim().length>0 || client.voiceState==="listening" || client.liveActive))
                    background: Rectangle { radius: width/2; color: sendButton.hovered ? Tokens.hover : "transparent"; border.width: sendButton.activeFocus ? 1 : 0; border.color: Tokens.text }
                    contentItem: Item { Pearl { anchors.fill: parent; visible: !client.busy; opacity: sendButton.enabled ? 1 : .35 } Image { anchors.centerIn: parent; width: 18; height: 18; source: "../assets/icons/stop.svg"; visible: client.busy } }
                    onClicked: client.busy || composer.text.trim().length ? sendAction.trigger() : win.openLive()
                }
                InlineLive { objectName: "livePanel"; anchors.fill: parent; host: win; visible: win.liveOpen }
            }
            Row {
                id: recordingStatus; visible: !win.liveOpen && (client.dictationBusy || client.dictationState === "ready" || client.busy || client.liveActive)
                anchors.top: composerPanel.bottom; anchors.topMargin: 4; anchors.horizontalCenter: composerPanel.horizontalCenter; spacing: 12
                property int seconds: 0
                Timer { interval: 1000; repeat: true; running: client.dictationState === "recording"; onTriggered: parent.seconds++ }
                Connections { target: client; function onChanged() { if(client.dictationState !== "recording") recordingStatus.seconds=0 } }
                Text { color: Tokens.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter; text: win.t(client.dictationBusy ? (client.dictationState === "recording" ? "Recording" : "Transcribing") : client.dictationState === "ready" ? "Draft ready" : client.busy ? "Onyx is responding" : client.liveActive ? "Live is on" : "") + (client.dictationState === "recording" ? " · "+parent.seconds+" s" : "") }
                SoftButton { height: 24; visible: client.dictationBusy; text: win.t("Cancel recording"); onClicked: client.cancelDictation() }
            }
            SoftButton { visible: !client.ready; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: composerPanel.top; text: win.t("Onyx is temporarily unavailable")+" · "+win.t("Retry"); onClicked: client.retry() }
        }
        AppsPage { host: win; visible: win.section==="Apps"; Layout.fillWidth: true; Layout.fillHeight: true }
        SettingsDialog { id: settings; host: win; visible: win.section==="Settings"; Layout.fillWidth: true; Layout.fillHeight: true }
    }
    Menu { id: attachmentMenu; x: center.x+20; y: center.y+composerPanel.y-110
        MenuItem { text: win.t("Choose a text file…"); onTriggered: { filePicker.nameFilters=["Text files (*.txt *.md *.py *.json *.csv *.log *.qml *.js *.html *.css)"]; filePicker.open() } }
    }
    FileDialog { id: filePicker; title: win.t("Choose a text file…"); fileMode: FileDialog.OpenFile; onAccepted: client.attachFile(selectedFile) }
    Menu { id: chatMenu; objectName: "chatMenu"; popupType: Popup.Item; width: 238; padding: 6
        background: Rectangle { color: Tokens.panel; radius: 12; border.color: Tokens.border }
        ChatMenuItem { text: win.t("Rename chat"); glyph: "edit"; onTriggered: { renameInput.text=win.menuTarget.title; renameDialog.open() } }
        ChatMenuItem { text: win.t(win.menuTarget.pinned ? "Unpin chat" : "Pin chat"); glyph: "pin"; onTriggered: client.pinChat(win.menuTarget.conversation_id,!win.menuTarget.pinned) }
        Menu { title: win.t("Move to project")
            MenuItem { text: win.t("No project"); onTriggered: client.moveChat(win.menuTarget.conversation_id,"") }
            Repeater { model: client.projects
                delegate: MenuItem { required property var modelData; text: modelData.title; onTriggered: client.moveChat(win.menuTarget.conversation_id,modelData.project_id) }
            }
        }
        ChatMenuItem { text: win.t(win.menuTarget.archived ? "Restore chat" : "Archive chat"); glyph: "archive"; onTriggered: client.archiveChat(win.menuTarget.conversation_id,!win.menuTarget.archived) }
        MenuSeparator { }
        ChatMenuItem { text: win.t("Delete chat"); glyph: "trash"; destructive: true; onTriggered: deleteDialog.open() }
    }
    Dialog { id: deleteDialog; objectName: "deleteDialog"; anchors.centerIn: parent; width: Math.min(480,win.width-40); modal: true; title: win.t("Delete chat")
        onOpened: deleteCancel.forceActiveFocus()
        background: Rectangle { color: Tokens.panel; radius: 12; border.color: Tokens.border }
        contentItem: ColumnLayout { spacing: 14
            Text { Layout.fillWidth: true; text: win.menuTarget.title || ""; color: Tokens.text; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: win.t("Messages and drafts will be deleted. Global memory, task journal, backups and original files remain."); color: Tokens.secondary; wrapMode: Text.Wrap }
            RowLayout {
                SoftButton { id: deleteCancel; text: win.t("Cancel"); onClicked: deleteDialog.close() }
                SoftButton { text: win.t("Delete chat"); onClicked: { client.deleteChat(win.menuTarget.conversation_id); deleteDialog.close() } }
            }
        }
    }
    Dialog { id: notice; property string text: ""; anchors.centerIn: parent; width: Math.min(480,win.width-60); modal: true; standardButtons: Dialog.Ok; title: "Onyx"; contentItem: Text { text: notice.text; color: Tokens.text; wrapMode: Text.Wrap } background: Rectangle { color: Tokens.panel; radius: 16; border.color: Tokens.border } }

    Dialog {
        id: renameDialog; anchors.centerIn: parent; width: 420; modal: true; title: win.t("Rename chat")
        palette.windowText: Tokens.text
        background: Rectangle { color: Tokens.panel; radius: Tokens.radius; border.color: Tokens.border }
        contentItem: ColumnLayout {
            TextField { id: renameInput; Layout.fillWidth: true; maximumLength: 100; color: Tokens.text; selectByMouse: true; background: Rectangle { color: Tokens.background; radius: 8 } }
            RowLayout { SoftButton { text: win.t("Rename"); onClicked: { client.renameChat(win.menuTarget.conversation_id,renameInput.text); renameDialog.close() } } SoftButton { text: win.t("Cancel"); onClicked: renameDialog.close() } }
        }
    }
    Dialog {
        id: projectDialog; property string projectId: ""; anchors.centerIn: parent; width: Math.min(440,win.width-40); modal: true
        title: win.t(projectId ? "Rename project" : "New project")
        background: Rectangle { color: Tokens.panel; border.color: Tokens.border; radius: 12 }
        contentItem: ColumnLayout {
            TextField { id: projectTitle; Layout.fillWidth: true; placeholderText: win.t("Project name"); maximumLength: 100 }
            TextField { id: projectPath; Layout.fillWidth: true; visible: !projectDialog.projectId; placeholderText: win.t("Workspace folder (optional)") }
            RowLayout {
                SoftButton { text: win.t(projectDialog.projectId ? "Save" : "Create"); enabled: !!projectTitle.text.trim(); onClicked: {client.saveProject(projectTitle.text,projectDialog.projectId,projectPath.text);projectDialog.close()} }
                SoftButton { text: win.t("Cancel"); onClicked: projectDialog.close() }
            }
        }
    }

}
