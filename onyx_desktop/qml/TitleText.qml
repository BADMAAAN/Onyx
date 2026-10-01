import QtQuick
Text {
    id: titleText
    property bool colorize: false
    font.family: Tokens.font; font.pixelSize: Tokens.heroSize; font.weight: Font.Bold
    color: colorize ? "#bcd6fc" : Tokens.text
    layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
    layer.effect: ShaderEffect {
        property real gradientAmount: titleText.colorize ? 1 : 0
        property vector2d sampleStep: Qt.vector2d(1.8/Math.max(1,titleText.width),1.8/Math.max(1,titleText.height))
        fragmentShader: "../assets/title.frag.qsb"
    }
}
