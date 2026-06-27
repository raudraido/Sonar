#include "scratchwaveformitem.h"

#include <QSGGeometryNode>
#include <QSGGeometry>
#include <QSGVertexColorMaterial>

#include <algorithm>
#include <cmath>

ScratchWaveformItem::ScratchWaveformItem(QQuickItem *parent) : QQuickItem(parent) {
    setFlag(ItemHasContents, true);
}

void ScratchWaveformItem::setSamples(const QVariantList &v) {
    m_samplesVariant = v;
    m_samples.clear();
    m_samples.reserve(v.size());
    for (const QVariant &x : v) {
        m_samples.push_back(x.toDouble());
    }
    emit samplesChanged();
    update();
}

namespace {
void toDoubleVector(const QVariantList &v, QVector<double> &out) {
    out.clear();
    out.reserve(v.size());
    for (const QVariant &x : v) out.push_back(x.toDouble());
}
}

void ScratchWaveformItem::setSamplesLow(const QVariantList &v) {
    m_samplesLowVariant = v;
    toDoubleVector(v, m_samplesLow);
    emit samplesLowChanged();
    update();
}

void ScratchWaveformItem::setSamplesMid(const QVariantList &v) {
    m_samplesMidVariant = v;
    toDoubleVector(v, m_samplesMid);
    emit samplesMidChanged();
    update();
}

void ScratchWaveformItem::setSamplesHigh(const QVariantList &v) {
    m_samplesHighVariant = v;
    toDoubleVector(v, m_samplesHigh);
    emit samplesHighChanged();
    update();
}

void ScratchWaveformItem::setDurationMs(double v) {
    v = std::max(1.0, v);
    if (m_durationMs == v) return;
    m_durationMs = v;
    emit durationMsChanged();
    update();
}

void ScratchWaveformItem::setBeatGridBpm(double v) {
    if (m_beatGridBpm == v) return;
    m_beatGridBpm = v;
    emit beatGridBpmChanged();
    update();
}

void ScratchWaveformItem::setBeatGridAnchorMs(double v) {
    if (m_beatGridAnchorMs == v) return;
    m_beatGridAnchorMs = v;
    emit beatGridAnchorMsChanged();
    update();
}

void ScratchWaveformItem::setHasRealData(bool v) {
    if (m_hasRealData == v) return;
    m_hasRealData = v;
    emit hasRealDataChanged();
    update();
}

void ScratchWaveformItem::setCurrentIndex(double v) {
    if (m_currentIndex == v) return;
    m_currentIndex = v;
    emit currentIndexChanged();
    update();
}

void ScratchWaveformItem::setPixelsPerSample(double v) {
    v = std::max(0.001, v);
    if (m_pixelsPerSample == v) return;
    m_pixelsPerSample = v;
    emit pixelsPerSampleChanged();
    update();
}

void ScratchWaveformItem::setHue(double v) {
    if (m_hue == v) return;
    m_hue = v;
    emit hueChanged();
    update();
}

namespace {

struct Rgb {
    double r, g, b;
};

// Ported from the old QML/JS (and numpy) HSV->RGB helper — h: 0-359, s/v: 0-255.
Rgb hsvToRgb(double h, double s, double v) {
    const double hi = h / 60.0;
    int i = static_cast<int>(std::floor(hi)) % 6;
    if (i < 0) i += 6;
    const double f = hi - std::floor(hi);
    const double vv = v / 255.0;
    const double ss = s / 255.0;
    const double p = vv * (1.0 - ss);
    const double q = vv * (1.0 - f * ss);
    const double t = vv * (1.0 - (1.0 - f) * ss);
    double r, g, b;
    switch (i) {
        case 0: r = vv; g = t;  b = p;  break;
        case 1: r = q;  g = vv; b = p;  break;
        case 2: r = p;  g = vv; b = t;  break;
        case 3: r = p;  g = q;  b = vv; break;
        case 4: r = t;  g = p;  b = vv; break;
        default: r = vv; g = p; b = q; break;
    }
    return {std::clamp(r, 0.0, 1.0), std::clamp(g, 0.0, 1.0), std::clamp(b, 0.0, 1.0)};
}

} // namespace

QSGNode *ScratchWaveformItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) {
    auto *node = static_cast<QSGGeometryNode *>(oldNode);

    const int w = std::max(1, static_cast<int>(std::ceil(width())));
    const int h = std::max(1, static_cast<int>(std::ceil(height())));
    const qsizetype total = m_samples.size();

    if (!m_hasRealData || total < 2) {
        if (node) {
            node->geometry()->allocate(0);
            node->markDirty(QSGNode::DirtyGeometry);
        }
        return node;
    }

    const double centerX0 = w / 2.0;
    const double centerY0 = h / 2.0;
    const double maxBarH0 = (h / 2.0) * 0.90;
    const double baseHue = m_hue >= 0 ? m_hue * 360.0 : 150.0;
    const double cx = centerX0;

    // Per-band (low/mid/high) coloring — mirrors Mixxx's HSV/RGB waveform
    // renderers (src/waveform/renderers/allshader/waveformrendererhsv.cpp,
    // waveformrendererrgb.cpp upstream): kicks/bass read green, mids blue,
    // transients/highs magenta, mixed by each band's share of the column's
    // total energy. Only used once generate_waveform_bands' arrays have
    // landed and line up sample-for-sample with m_samples; otherwise this
    // falls back to the original single-hue-shift-by-amplitude coloring.
    const bool hasBands = m_samplesLow.size() == total &&
            m_samplesMid.size() == total && m_samplesHigh.size() == total;
    constexpr Rgb kLowColor  = {0.25, 1.00, 0.35};
    constexpr Rgb kMidColor  = {0.25, 0.55, 1.00};
    constexpr Rgb kHighColor = {1.00, 0.25, 0.85};

    // Math mirrors the old per-pixel JS/numpy renderer exactly (its
    // user_picked flag was always false in practice, so that branch is
    // folded in directly rather than plumbed through as a parameter).
    // Up to 6 vertices (2 triangles) per visible column.
    QVector<QSGGeometry::ColoredPoint2D> verts;
    verts.reserve(w * 6);

    for (int x = 0; x < w; ++x) {
        const double exactIndex = m_currentIndex + (x - centerX0) / m_pixelsPerSample;
        if (exactIndex < 0 || exactIndex >= total - 1) continue;

        const double fade = cx > 0
                ? std::max(0.0, 1.0 - std::pow(std::abs(x - cx) / cx, 1.6))
                : 0.0;
        const bool isLeft = x < centerX0;
        const double alpha = isLeft ? fade : fade * (180.0 / 255.0);
        if (alpha < (1.0 / 255.0)) continue;

        const qsizetype idx1 = std::clamp<qsizetype>(
                static_cast<qsizetype>(std::floor(exactIndex)), 0, total - 2);
        const double frac = exactIndex - static_cast<double>(idx1);
        const double rawVal = m_samples[idx1] + (m_samples[idx1 + 1] - m_samples[idx1]) * frac;

        // Light neighborhood smoothing for the bar height only (color/hue
        // below still uses rawVal, so transients keep their punch) — quiet
        // stretches between peaks otherwise collapse to near-0px columns,
        // which reads as disconnected floating diamonds rather than a
        // continuous waveform. A small minimum floor on top keeps even
        // silent columns showing a thin baseline instead of vanishing.
        const qsizetype smoothSpan = 3;
        double smoothSum = 0.0;
        int smoothCount = 0;
        for (qsizetype k = -smoothSpan; k <= smoothSpan; ++k) {
            const qsizetype si = idx1 + k;
            if (si < 0 || si >= total) continue;
            smoothSum += m_samples[si];
            ++smoothCount;
        }
        const double smoothedVal = smoothCount > 0 ? smoothSum / smoothCount : rawVal;

        // Linear, not squared — Mixxx's own height mapping is pure linear
        // (heightFactor * peakValue, waveformrendererfiltered.cpp upstream),
        // no compression curve. Squaring here was actively fighting the
        // peak-detection fix in generate_waveform/_bands above: it pulled
        // mid/quiet amplitudes back down toward zero, which is exactly the
        // "quiet parts barely render" problem peak detection was meant to
        // fix in the first place.
        const double barH = std::max(1.5, smoothedVal * maxBarH0);

        Rgb rgb;
        if (hasBands) {
            const double lowV  = m_samplesLow[idx1]  + (m_samplesLow[idx1 + 1]  - m_samplesLow[idx1])  * frac;
            const double midV  = m_samplesMid[idx1]  + (m_samplesMid[idx1 + 1]  - m_samplesMid[idx1])  * frac;
            const double highV = m_samplesHigh[idx1] + (m_samplesHigh[idx1 + 1] - m_samplesHigh[idx1]) * frac;
            const double bandSum = lowV + midV + highV;
            const double wl = bandSum > 0.0 ? lowV  / bandSum : 1.0 / 3.0;
            const double wm = bandSum > 0.0 ? midV  / bandSum : 1.0 / 3.0;
            const double wh = bandSum > 0.0 ? highV / bandSum : 1.0 / 3.0;
            const double brightnessFactor = isLeft ? (0.47 + 0.53 * fade) : (0.27 + 0.51 * fade);
            rgb.r = (wl * kLowColor.r + wm * kMidColor.r + wh * kHighColor.r) * brightnessFactor;
            rgb.g = (wl * kLowColor.g + wm * kMidColor.g + wh * kHighColor.g) * brightnessFactor;
            rgb.b = (wl * kLowColor.b + wm * kMidColor.b + wh * kHighColor.b) * brightnessFactor;
        } else {
            const double brightness = std::clamp(
                    isLeft ? (120.0 + 135.0 * fade) : (70.0 + 130.0 * fade), 0.0, 255.0);
            const double hueShift = std::floor(20.0 * rawVal);
            const double finalHue = std::fmod(std::fmod(baseHue + hueShift, 360.0) + 360.0, 360.0);
            const double saturation = std::clamp(255.0 * std::max(0.3, 1.0 - rawVal * 0.6), 0.0, 255.0);
            rgb = hsvToRgb(finalHue, saturation, brightness);
        }
        const auto R = static_cast<quint8>(std::round(std::clamp(rgb.r, 0.0, 1.0) * 255.0));
        const auto G = static_cast<quint8>(std::round(std::clamp(rgb.g, 0.0, 1.0) * 255.0));
        const auto B = static_cast<quint8>(std::round(std::clamp(rgb.b, 0.0, 1.0) * 255.0));
        const auto A = static_cast<quint8>(std::round(std::clamp(alpha, 0.0, 1.0) * 255.0));

        const auto top = static_cast<float>(centerY0 - barH);
        const auto bottom = static_cast<float>(centerY0 + barH);
        const auto xf0 = static_cast<float>(x);
        const auto xf1 = static_cast<float>(x + 1);

        verts.append({xf0, top, R, G, B, A});
        verts.append({xf1, top, R, G, B, A});
        verts.append({xf0, bottom, R, G, B, A});
        verts.append({xf1, top, R, G, B, A});
        verts.append({xf1, bottom, R, G, B, A});
        verts.append({xf0, bottom, R, G, B, A});
    }

    // Beat-grid lines (Mixxx-style) — anchorMs + n*(60000/bpm) for every
    // beat falling within the visible sample window. Drawn last so they
    // composite on top of the waveform columns above. m_durationMs/total
    // convert a beat's ms position into the same fractional-sample-index
    // space the waveform columns above are positioned in.
    if (m_beatGridBpm > 0.0 && m_durationMs > 0.0) {
        const double intervalMs = 60000.0 / m_beatGridBpm;
        const double msPerIndex = m_durationMs / static_cast<double>(total - 1);
        const double leftIndex  = m_currentIndex + (0 - centerX0) / m_pixelsPerSample;
        const double rightIndex = m_currentIndex + (w - centerX0) / m_pixelsPerSample;
        const double msStart = leftIndex  * msPerIndex;
        const double msEnd   = rightIndex * msPerIndex;
        const long long kStart = static_cast<long long>(std::floor((msStart - m_beatGridAnchorMs) / intervalMs)) - 1;
        const long long kEnd   = static_cast<long long>(std::ceil((msEnd - m_beatGridAnchorMs) / intervalMs)) + 1;

        const quint8 gridA = static_cast<quint8>(std::round(0.32 * 255.0));
        for (long long k = kStart; k <= kEnd; ++k) {
            const double beatMs = m_beatGridAnchorMs + static_cast<double>(k) * intervalMs;
            if (beatMs < 0.0 || beatMs > m_durationMs) continue;
            const double beatIndex = beatMs / msPerIndex;
            const double xCenter = centerX0 + (beatIndex - m_currentIndex) * m_pixelsPerSample;
            if (xCenter < -1.0 || xCenter > w + 1.0) continue;

            const auto gx0 = static_cast<float>(xCenter - 0.6);
            const auto gx1 = static_cast<float>(xCenter + 0.6);
            verts.append({gx0, 0.0f, 255, 255, 255, gridA});
            verts.append({gx1, 0.0f, 255, 255, 255, gridA});
            verts.append({gx0, static_cast<float>(h), 255, 255, 255, gridA});
            verts.append({gx1, 0.0f, 255, 255, 255, gridA});
            verts.append({gx1, static_cast<float>(h), 255, 255, 255, gridA});
            verts.append({gx0, static_cast<float>(h), 255, 255, 255, gridA});
        }
    }

    if (!node) {
        node = new QSGGeometryNode();
        auto *geometry = new QSGGeometry(QSGGeometry::defaultAttributes_ColoredPoint2D(), 0);
        geometry->setDrawingMode(QSGGeometry::DrawTriangles);
        node->setGeometry(geometry);
        node->setFlag(QSGNode::OwnsGeometry);
        auto *material = new QSGVertexColorMaterial();
        node->setMaterial(material);
        node->setFlag(QSGNode::OwnsMaterial);
    }

    QSGGeometry *geometry = node->geometry();
    geometry->allocate(verts.size());
    geometry->setDrawingMode(QSGGeometry::DrawTriangles);
    std::copy(verts.begin(), verts.end(), geometry->vertexDataAsColoredPoint2D());
    node->markDirty(QSGNode::DirtyGeometry);

    return node;
}
