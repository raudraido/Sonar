#pragma once

#include <QQuickItem>
#include <QVariant>
#include <QVector>

// Renders the scratch-mode (displayMode 0) waveform as one batched GPU draw
// call — a single QSGGeometryNode of colored triangles (2 per visible
// column), via QSGVertexColorMaterial (Qt's built-in equivalent of Mixxx's
// RGBMaterial — see src/waveform/renderers/allshader/waveformrendererhsv.cpp
// upstream in github.com/mixxxdj/mixxx for the technique this mirrors).
//
// Three pure-QML/Python approaches were tried first and all hit a ceiling
// well below this: a Canvas with putImageData (pixel blit doesn't reliably
// sync to the GPU FBO texture Canvas uses on Linux — invisible waveform), a
// Repeater of ~700 individually-bound Rectangle items (~10fps — hundreds of
// scene-graph bindings re-evaluating per frame), and a QQuickPaintedItem
// rebuilding a QImage every update() (still capped well below the display's
// real refresh rate — see footer_bridge.py's now-removed ScratchWaveformItem
// for the numpy version of this same math). This class is the actual
// custom-scene-graph-node path those approaches couldn't reach from
// pure Python/QML.
class ScratchWaveformItem : public QQuickItem {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QVariantList samples READ samples WRITE setSamples NOTIFY samplesChanged)
    Q_PROPERTY(bool hasRealData READ hasRealData WRITE setHasRealData NOTIFY hasRealDataChanged)
    Q_PROPERTY(double currentIndex READ currentIndex WRITE setCurrentIndex NOTIFY currentIndexChanged)
    Q_PROPERTY(double pixelsPerSample READ pixelsPerSample WRITE setPixelsPerSample NOTIFY pixelsPerSampleChanged)
    Q_PROPERTY(double hue READ hue WRITE setHue NOTIFY hueChanged)

public:
    explicit ScratchWaveformItem(QQuickItem *parent = nullptr);

    QVariantList samples() const { return m_samplesVariant; }
    void setSamples(const QVariantList &v);

    bool hasRealData() const { return m_hasRealData; }
    void setHasRealData(bool v);

    double currentIndex() const { return m_currentIndex; }
    void setCurrentIndex(double v);

    double pixelsPerSample() const { return m_pixelsPerSample; }
    void setPixelsPerSample(double v);

    double hue() const { return m_hue; }
    void setHue(double v);

signals:
    void samplesChanged();
    void hasRealDataChanged();
    void currentIndexChanged();
    void pixelsPerSampleChanged();
    void hueChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;

private:
    QVariantList m_samplesVariant;
    QVector<double> m_samples;
    bool m_hasRealData = false;
    double m_currentIndex = 0.0;
    double m_pixelsPerSample = 1.5;
    double m_hue = -1.0;
};
