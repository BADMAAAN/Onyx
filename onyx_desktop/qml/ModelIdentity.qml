import QtQuick
Item {
    property string brand: "custom"
    property string asset: ({"qwen":"qwen.png","openai":"openai.png","meta":"meta-llama.png","deepseek":"deepseek.png","huggingface":"huggingface.svg","smollm":"huggingface.svg"})[brand] || ""
    Image { anchors.fill: parent; fillMode: Image.PreserveAspectFit; visible: !!asset; source: asset ? "../assets/"+asset : "" }
    Text { anchors.centerIn: parent; visible: !asset; text: "◇"; color: Tokens.secondary; font.pixelSize: 20 }
}
