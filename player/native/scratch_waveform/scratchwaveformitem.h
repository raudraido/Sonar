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
    Q_PROPERTY(QVariantList samplesLow READ samplesLow WRITE setSamplesLow NOTIFY samplesLowChanged)
    Q_PROPERTY(QVariantList samplesMid READ samplesMid WRITE setSamplesMid NOTIFY samplesMidChanged)
    Q_PROPERTY(QVariantList samplesHigh READ samplesHigh WRITE setSamplesHigh NOTIFY samplesHighChanged)
    Q_PROPERTY(bool hasRealData READ hasRealData WRITE setHasRealData NOTIFY hasRealDataChanged)
    Q_PROPERTY(double currentIndex READ currentIndex WRITE setCurrentIndex NOTIFY currentIndexChanged)
    Q_PROPERTY(double pixelsPerSample READ pixelsPerSample WRITE setPixelsPerSample NOTIFY pixelsPerSampleChanged)
    Q_PROPERTY(double hue READ hue WRITE setHue NOTIFY hueChanged)
    Q_PROPERTY(double durationMs READ durationMs WRITE setDurationMs NOTIFY durationMsChanged)
    Q_PROPERTY(QVariantList beatPositionsMs READ beatPositionsMs WRITE setBeatPositionsMs NOTIFY beatPositionsMsChanged)
    Q_PROPERTY(int downbeatOffset READ downbeatOffset WRITE setDownbeatOffset NOTIFY downbeatOffsetChanged)

public:
    explicit ScratchWaveformItem(QQuickItem *parent = nullptr);

    QVariantList samples() const { return m_samplesVariant; }
    void setSamples(const QVariantList &v);

    QVariantList samplesLow() const { return m_samplesLowVariant; }
    void setSamplesLow(const QVariantList &v);
    QVariantList samplesMid() const { return m_samplesMidVariant; }
    void setSamplesMid(const QVariantList &v);
    QVariantList samplesHigh() const { return m_samplesHighVariant; }
    void setSamplesHigh(const QVariantList &v);

    bool hasRealData() const { return m_hasRealData; }
    void setHasRealData(bool v);

    double currentIndex() const { return m_currentIndex; }
    void setCurrentIndex(double v);

    double pixelsPerSample() const { return m_pixelsPerSample; }
    void setPixelsPerSample(double v);

    double hue() const { return m_hue; }
    void setHue(double v);

    double durationMs() const { return m_durationMs; }
    void setDurationMs(double v);

    QVariantList beatPositionsMs() const { return m_beatPositionsVariant; }
    void setBeatPositionsMs(const QVariantList &v);

    int downbeatOffset() const { return m_downbeatOffset; }
    void setDownbeatOffset(int v);

signals:
    void samplesChanged();
    void samplesLowChanged();
    void samplesMidChanged();
    void samplesHighChanged();
    void hasRealDataChanged();
    void currentIndexChanged();
    void pixelsPerSampleChanged();
    void hueChanged();
    void durationMsChanged();
    void beatPositionsMsChanged();
    void downbeatOffsetChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;

private:
    QVariantList m_samplesVariant;
    QVector<double> m_samples;
    QVariantList m_samplesLowVariant, m_samplesMidVariant, m_samplesHighVariant;
    QVector<double> m_samplesLow, m_samplesMid, m_samplesHigh;
    bool m_hasRealData = false;
    double m_currentIndex = 0.0;
    double m_pixelsPerSample = 1.5;
    double m_hue = -1.0;
    double m_durationMs = 1.0;
    QVariantList m_beatPositionsVariant;
    QVector<double> m_beatPositionsMs;  // sorted ascending — real detected beat onsets
    int m_downbeatOffset = 0;  // which beat (0-3) is bar 1 — see audio_core.cpp's metronome_downbeat_offset
};
