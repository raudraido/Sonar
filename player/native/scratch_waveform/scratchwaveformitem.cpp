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

void ScratchWaveformItem::setBeatPositionsMs(const QVariantList &v) {
    m_beatPositionsVariant = v;
    toDoubleVector(v, m_beatPositionsMs);
    emit beatPositionsMsChanged();
    update();
}

void ScratchWaveformItem::setDownbeatOffset(int v) {
    v = ((v % 4) + 4) % 4;
    if (m_downbeatOffset == v) return;
    m_downbeatOffset = v;
    emit downbeatOffsetChanged();
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
    // waveformrendererrgb.cpp upstream): each band gets its own hue, mixed
    // by its share of the column's total energy. Hues are derived from the
    // app's accent color (baseHue) rather than fixed green/blue/magenta, so
    // the waveform follows the user's theme — bass/kicks read as the accent
    // color itself, mid/high are hue-shifted ±40° from it. Only used once
    // generate_waveform_bands' arrays have landed and line up sample-for-
    // sample with m_samples; otherwise this falls back to the original
    // single-hue-shift-by-amplitude coloring.
    const bool hasBands = m_samplesLow.size() == total &&
            m_samplesMid.size() == total && m_samplesHigh.size() == total;
    const Rgb kLowColor  = hsvToRgb(baseHue, 215.0, 255.0);
    const Rgb kMidColor  = hsvToRgb(std::fmod(baseHue + 40.0, 360.0), 215.0, 255.0);
    const Rgb kHighColor = hsvToRgb(std::fmod(baseHue + 320.0, 360.0), 215.0, 255.0);

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

        // No neighbor smoothing — m_samples is already RMS-per-bucket (see
        // generate_waveform in audio_core.cpp), which never truly collapses
        // to 0 between hits the way the old peak/mean-abs data could.
        // Averaging neighboring buckets together on top of that just blurs
        // each kick's actual attack/decay shape into a soft blob instead of
        // the sharp "shark-tooth" silhouette real transients have — exactly
        // the flattened, undersized look that didn't match Mixxx's render.
        const double smoothedVal = rawVal;

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

    // Beat-grid lines — one at each *actually detected* beat position
    // (m_beatPositionsMs, real onsets from get_file_beat_grid in
    // audio_core.cpp) that falls within the visible sample window, instead
    // of extrapolating evenly-spaced lines from a single anchor+bpm — that
    // approach assumed perfectly constant tempo from one point and was
    // vulnerable to the tracker's own octave (half/double-tempo) errors
    // visibly drifting off the real transients. Drawn last so they
    // composite on top of the waveform columns above. m_durationMs/total
    // convert a beat's ms position into the same fractional-sample-index
    // space the waveform columns above are positioned in. m_beatPositionsMs
    // is sorted ascending, so binary-search the visible ms range instead of
    // scanning every beat in the track on every repaint.
    if (!m_beatPositionsMs.isEmpty() && m_durationMs > 0.0) {
        const double msPerIndex = m_durationMs / static_cast<double>(total - 1);
        const double leftIndex  = m_currentIndex + (0 - centerX0) / m_pixelsPerSample;
        const double rightIndex = m_currentIndex + (w - centerX0) / m_pixelsPerSample;
        const double msStart = std::min(leftIndex, rightIndex) * msPerIndex;
        const double msEnd   = std::max(leftIndex, rightIndex) * msPerIndex;

        const auto beginIt = std::lower_bound(m_beatPositionsMs.begin(), m_beatPositionsMs.end(), msStart);
        const auto endIt   = std::upper_bound(m_beatPositionsMs.begin(), m_beatPositionsMs.end(), msEnd);

        // Every bar's first beat (m_downbeatOffset gives the 0-3 offset of
        // bar 1's own first beat, same alignment as the metronome's
        // tick/tock in audio_core.cpp) is drawn brighter/wider and tinted
        // with the master accent color (baseHue) so each bar boundary reads
        // clearly against the plain white regular-beat lines.
        const quint8 gridA = static_cast<quint8>(std::round(0.32 * 255.0));
        const quint8 downbeatA = static_cast<quint8>(std::round(0.65 * 255.0));
        const Rgb downbeatRgb = hsvToRgb(baseHue, 200.0, 255.0);
        const auto downbeatR = static_cast<quint8>(std::round(downbeatRgb.r * 255.0));
        const auto downbeatG = static_cast<quint8>(std::round(downbeatRgb.g * 255.0));
        const auto downbeatB = static_cast<quint8>(std::round(downbeatRgb.b * 255.0));
        for (auto it = beginIt; it != endIt; ++it) {
            const double beatMs = *it;
            const double beatIndex = beatMs / msPerIndex;
            const double xCenter = centerX0 + (beatIndex - m_currentIndex) * m_pixelsPerSample;
            if (xCenter < -1.0 || xCenter > w + 1.0) continue;

            const auto rawIndex = static_cast<long long>(it - m_beatPositionsMs.begin());
            const bool isDownbeat = ((rawIndex % 4) - m_downbeatOffset + 4) % 4 == 0;
            const double halfWidth = isDownbeat ? 1.1 : 0.6;
            const quint8 R = isDownbeat ? downbeatR : 255;
            const quint8 G = isDownbeat ? downbeatG : 255;
            const quint8 B = isDownbeat ? downbeatB : 255;
            const quint8 A = isDownbeat ? downbeatA : gridA;

            const auto gx0 = static_cast<float>(xCenter - halfWidth);
            const auto gx1 = static_cast<float>(xCenter + halfWidth);
            verts.append({gx0, 0.0f, R, G, B, A});
            verts.append({gx1, 0.0f, R, G, B, A});
            verts.append({gx0, static_cast<float>(h), R, G, B, A});
            verts.append({gx1, 0.0f, R, G, B, A});
            verts.append({gx1, static_cast<float>(h), R, G, B, A});
            verts.append({gx0, static_cast<float>(h), R, G, B, A});
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
