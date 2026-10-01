import QtQuick
import QtQuick.Controls.Basic
ListView {
    id: sidebar
    required property var host
    property string query: ""
    signal createProject()
    signal editProject(var project)
    property bool dragActive: false
    property string draggedId: ""
    property string draggedTitle: ""
    property var projectTarget: ({})
    function openProjectMenu(project) { projectTarget=project;projectMenu.popup() }
    Menu {
        id: projectMenu; width: 200; padding: 6
        background: Rectangle { color: Tokens.panel; border.color: Tokens.border; radius: 10 }
        ChatMenuItem { text: sidebar.host.t("Rename project"); glyph: "edit"; onTriggered: sidebar.editProject(sidebar.projectTarget) }
        ChatMenuItem { text: sidebar.host.t(sidebar.projectTarget.closed ? "Expand" : "Collapse"); glyph: "folder"; onTriggered: client.toggleSection(sidebar.projectTarget.project_id) }
    }
    function dragAt(source, point) {
        const p=source.mapToItem(dragPreview.parent,point.x,point.y)
        dragPreview.x=p.x-16;dragPreview.y=p.y-16
    }
    Rectangle {
        id: dragPreview; parent: sidebar.host.contentItem
        width: 180; height: 32; radius: 7; z: 1000
        visible: sidebar.dragActive; color: Tokens.elevated; border.color: Tokens.accent
        property string conversationId: sidebar.draggedId
        Drag.active: sidebar.dragActive; Drag.source: dragPreview; Drag.keys: ["onyx-conversation"]
        Drag.hotSpot.x: 16; Drag.hotSpot.y: 16
        Text { anchors.fill: parent; anchors.margins: 8; text: sidebar.draggedTitle; color: Tokens.text; elide: Text.ElideRight; font.pixelSize: 12 }
    }
    clip: true; spacing: 1; cacheBuffer: 100
    ScrollBar.vertical: ScrollBar { }
    function rows() {
        const collapsed=client.sidebarCollapsed, result=[], chats=client.chats.filter(c=>!query || c.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
        function header(key,title) { result.push({kind:"section",key:key,title:host.t(title),closed:!!collapsed[key]}) }
        function chat(c) { result.push(Object.assign({kind:"chat",archived:client.archived},c)) }
        header("pinned","Pinned")
        if(!client.archived && !collapsed.pinned) chats.filter(c=>c.pinned).forEach(chat)
        header("projects","Projects")
        if(!collapsed.projects) {
            client.projects.forEach(p=>{
                result.push(Object.assign({kind:"project",key:p.project_id,closed:!!collapsed[p.project_id]},p))
                if(!client.archived && !collapsed[p.project_id]) chats.filter(c=>c.project_id===p.project_id && !c.pinned).forEach(chat)
            })
            result.push({kind:"create",title:host.t("New project")})
        }
        header("recent","Recent")
        if(!client.archived && !collapsed.recent) chats.filter(c=>!c.pinned && !c.project_id).forEach(chat)
        result.push({kind:"archive",title:host.t("Archive"),closed:!client.archived})
        if(client.archived) chats.forEach(chat)
        return result
    }
    model: rows()
    delegate: Button {
        id: row
        required property var modelData
        objectName: modelData.kind+":"+(modelData.conversation_id || modelData.project_id || modelData.key || "")
        width: sidebar.width; height: modelData.kind==="section" ? Tokens.rowHeight-2 : Tokens.rowHeight
        hoverEnabled: true; Accessible.name: modelData.title
        background: Rectangle {
            radius: 7
            color: projectDrop.containsDrag ? Tokens.hover : modelData.kind==="chat" && client.selected===modelData.conversation_id ? Tokens.elevated : row.hovered ? Tokens.hover : "transparent"
            border.width: projectDrop.containsDrag ? 1 : 0; border.color: Tokens.accent
        }
        contentItem: Item {
            Image { visible: modelData.kind==="project"; x: 3; anchors.verticalCenter: parent.verticalCenter; width: 15; height: 15; source: "../assets/icons/folder.svg" }
            Text {
                x: modelData.kind==="project" ? 24 : modelData.kind==="chat" ? (modelData.project_id && !modelData.pinned ? 18 : 8) : 4; anchors.verticalCenter: parent.verticalCenter; width: parent.width-x-26
                text: (modelData.kind==="section" || modelData.kind==="archive" ? (modelData.closed ? "›  " : "⌄  ") : modelData.kind==="create" ? "+  " : modelData.pinned ? "·  " : "")+modelData.title
                font.pixelSize: modelData.kind==="section" ? 11 : 13
                color: modelData.kind==="section" || modelData.kind==="create" ? Tokens.secondary : Tokens.text
                elide: Text.ElideRight
            }
        }
        onClicked: {
            if(modelData.kind==="chat") {host.section="Chat";host.following=true;client.select(modelData.conversation_id)}
            else if(modelData.kind==="create") sidebar.createProject()
            else if(modelData.kind==="archive") client.showArchive(!client.archived)
            else {if(client.archived) client.showArchive(false);client.toggleSection(modelData.key)}
        }
        IconButton {
            anchors.right: parent.right; width: 25; height: 30; iconName: "more"; iconSize: 14
            visible: modelData.kind==="chat" || modelData.kind==="project"; label: sidebar.host.t("Conversation options")
            background: Rectangle { radius: 5; color: parent.hovered ? Tokens.hover : "transparent" }
            onClicked: modelData.kind==="project" ? sidebar.openProjectMenu(modelData) : sidebar.host.openChatMenu(modelData)
        }
        DragHandler {
            id: chatDrag; target: null; enabled: modelData.kind==="chat"
            onActiveChanged: {
                if(active) {
                    sidebar.draggedId=modelData.conversation_id;sidebar.draggedTitle=modelData.title
                    sidebar.dragAt(row,centroid.position);sidebar.dragActive=true
                } else if(sidebar.dragActive) {
                    dragPreview.Drag.drop();sidebar.dragActive=false;sidebar.draggedId=""
                }
            }
            onCentroidChanged: if(active) sidebar.dragAt(row,centroid.position)
        }
        DropArea {
            id: projectDrop; anchors.fill: parent; enabled: modelData.kind==="project"
            keys: ["onyx-conversation"]
            onDropped: function(drop) {
                if(modelData.kind==="project" && drop.source && drop.source.conversationId) {
                    client.moveChat(drop.source.conversationId,modelData.project_id)
                    drop.acceptProposedAction()
                }
            }
        }
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.RightButton; onClicked: {
            if(modelData.kind==="chat") sidebar.host.openChatMenu(modelData)
            else if(modelData.kind==="project") sidebar.openProjectMenu(modelData)
        } }
        GraphiteToolTip { visible: row.hovered && !!modelData.workspace_path; text: modelData.workspace_path || "" }
    }
}
