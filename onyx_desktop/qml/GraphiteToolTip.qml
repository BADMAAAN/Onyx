import QtQuick
import QtQuick.Controls.Basic
ToolTip {
    id: tip
    delay: 600
    timeout: 5000
    padding: 9
    contentItem: Text { text: tip.text; color: Tokens.text; font.pixelSize: 12; wrapMode: Text.Wrap }
    background: Rectangle { color: Tokens.elevated; radius: 7; border.color: Tokens.border }
}
