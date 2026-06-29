#include "scratchwaveformitem.h"

#include <QQuickWindow>
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

    // Per-band (low/mid/high) coloring — mirrors Mixxx's actual "RGB"
    // overview renderer (allshader/waveformrendererrgb.cpp upstream): each
    // column's color is its three bands' reference colors mixed by each
    // band's own max level, then normalized by the largest resulting
    // component (see the hasBands branch below) so whichever band
    // dominates a column reads as a clearly distinct, fully saturated
    // color rather than a blended pastel. That only reads as "RGB" if the
    // three reference colors are actually far apart — a ±40° spread (this
    // renderer's original HSV-blend palette, kept for the non-banded
    // fallback look) leaves too little hue separation for normalize-by-max
    // to visibly distinguish bands. Spaced a full 120° apart (an even
    // three-way split of the hue wheel, like real RGB primaries) instead,
    // still anchored to the app's accent color rather than fixed red/
    // green/blue so it follows the user's theme. Only used once
    // generate_waveform_bands' arrays have landed and line up sample-for-
    // sample with m_samples; otherwise this falls back to the original
    // single-hue-shift-by-amplitude coloring.
    const bool hasBands = m_samplesLow.size() == total &&
            m_samplesMid.size() == total && m_samplesHigh.size() == total;
    const Rgb kLowColor  = hsvToRgb(baseHue, 255.0, 255.0);
    const Rgb kMidColor  = hsvToRgb(std::fmod(baseHue + 120.0, 360.0), 255.0, 255.0);
    const Rgb kHighColor = hsvToRgb(std::fmod(baseHue + 240.0, 360.0), 255.0, 255.0);

    // Math mirrors the old per-pixel JS/numpy renderer exactly (its
    // user_picked flag was always false in practice, so that branch is
    // folded in directly rather than plumbed through as a parameter).
    // Up to 6 vertices (2 triangles) per visible column.
    QVector<QSGGeometry::ColoredPoint2D> verts;
    verts.reserve(w * 6);

    for (int x = 0; x < w; ++x) {
        const double exactIndex = m_currentIndex + (x - centerX0) / m_pixelsPerSample;
        if (exactIndex < 0 || exactIndex >= total - 1) continue;

        const qsizetype idx1 = std::clamp<qsizetype>(
                static_cast<qsizetype>(std::floor(exactIndex)), 0, total - 2);
        const double frac = exactIndex - static_cast<double>(idx1);

        // indexSpan is how many sample buckets this one screen pixel
        // actually covers. Zoomed in (indexSpan <= 1, one bucket spans many
        // pixels), the plain two-point interpolation below is exact. Zoomed
        // out far enough that many buckets land on a single pixel, point-
        // sampling just two of them was the actual cause of the "flickering
        // waveform" bug: as m_currentIndex advances continuously during
        // playback, *which* two buckets a given pixel happens to land on
        // changes constantly, and in busy passages (lots of tiny bars —
        // high bucket-to-bucket variance) that means the rendered height
        // pops between a peak and a neighboring quiet bucket from one frame
        // to the next even though the underlying signal is only sliding by
        // a fraction of a pixel. Dragging never showed it because the user
        // is watching the motion, not staring at a near-static frame the
        // way slow playback scroll invites. Taking the max bucket value
        // actually spanned by the pixel instead makes the rendered value a
        // continuous function of scroll position — exactly how every real
        // waveform/DAW view downsamples for display (peak-per-pixel), and
        // it never has more than one bucket's worth of values to skip
        // between adjacent pixels regardless of zoom.
        const double indexSpan = 1.0 / m_pixelsPerSample;
        qsizetype idxHi = idx1 + 1;
        if (indexSpan > 1.0) {
            idxHi = std::clamp<qsizetype>(
                    static_cast<qsizetype>(std::ceil(exactIndex + indexSpan)), idx1 + 1, total - 1);
        }

        double rawVal;
        double lowV = 0.0, midV = 0.0, highV = 0.0;
        if (idxHi == idx1 + 1) {
            rawVal = m_samples[idx1] + (m_samples[idx1 + 1] - m_samples[idx1]) * frac;
            if (hasBands) {
                lowV  = m_samplesLow[idx1]  + (m_samplesLow[idx1 + 1]  - m_samplesLow[idx1])  * frac;
                midV  = m_samplesMid[idx1]  + (m_samplesMid[idx1 + 1]  - m_samplesMid[idx1])  * frac;
                highV = m_samplesHigh[idx1] + (m_samplesHigh[idx1 + 1] - m_samplesHigh[idx1]) * frac;
            }
        } else {
            rawVal = 0.0;
            for (qsizetype k = idx1; k <= idxHi; ++k) rawVal = std::max(rawVal, m_samples[k]);
            if (hasBands) {
                for (qsizetype k = idx1; k <= idxHi; ++k) {
                    lowV  = std::max(lowV,  m_samplesLow[k]);
                    midV  = std::max(midV,  m_samplesMid[k]);
                    highV = std::max(highV, m_samplesHigh[k]);
                }
            }
        }

        // No neighbor smoothing beyond the peak-per-pixel reduction above —
        // m_samples is already RMS-per-bucket (see generate_waveform in
        // audio_core.cpp), which never truly collapses to 0 between hits
        // the way the old peak/mean-abs data could. Averaging neighboring
        // buckets together on top of that just blurs each kick's actual
        // attack/decay shape into a soft blob instead of the sharp "shark-
        // tooth" silhouette real transients have — exactly the flattened,
        // undersized look that didn't match Mixxx's render.
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
            // Mixxx's actual "RGB" overview style (allshader/
            // waveformrendererrgb.cpp upstream): mix each band's reference
            // color weighted by that band's own max level, then *normalize*
            // the result by its largest component instead of averaging —
            // whichever band dominates a column pushes the color to full,
            // vivid saturation, instead of the soft blended-pastel look a
            // weighted average gives. No extra brightness/alpha dimming on
            // top — Mixxx's own bars are always drawn at full, undimmed
            // alpha, matching this renderer's bars now too (the past/future
            // edge fade this used to lean on for dimming has been removed).
            double red   = lowV * kLowColor.r + midV * kMidColor.r + highV * kHighColor.r;
            double green = lowV * kLowColor.g + midV * kMidColor.g + highV * kHighColor.g;
            double blue  = lowV * kLowColor.b + midV * kMidColor.b + highV * kHighColor.b;
            const double maxComponent = std::max({red, green, blue});
            if (maxComponent > 0.0) {
                red /= maxComponent;
                green /= maxComponent;
                blue /= maxComponent;
            }
            rgb = {red, green, blue};
        } else {
            const double hueShift = std::floor(20.0 * rawVal);
            const double finalHue = std::fmod(std::fmod(baseHue + hueShift, 360.0) + 360.0, 360.0);
            const double saturation = std::clamp(255.0 * std::max(0.3, 1.0 - rawVal * 0.6), 0.0, 255.0);
            rgb = hsvToRgb(finalHue, saturation, 255.0);
        }
        const auto R = static_cast<quint8>(std::round(std::clamp(rgb.r, 0.0, 1.0) * 255.0));
        const auto G = static_cast<quint8>(std::round(std::clamp(rgb.g, 0.0, 1.0) * 255.0));
        const auto B = static_cast<quint8>(std::round(std::clamp(rgb.b, 0.0, 1.0) * 255.0));
        const quint8 A = 255;

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

        // Mixxx's own beat-grid renderer (allshader/waveformrenderbeat.cpp,
        // upstream) doesn't antialias or feather these lines at all — it
        // snaps each line's x position to the nearest *device* pixel
        // (xBeatPoint = qRound(xBeatPoint * devicePixelRatio) / devicePixelRatio)
        // before drawing it at a crisp, fixed device-pixel width. A
        // continuously-moving subpixel-positioned hard edge is what strobes
        // as it crosses pixel boundaries (very visible at low zoom, where a
        // line crawls across the screen slowly enough for the eye to catch
        // each on/off pop); a line whose edges always land exactly on a
        // pixel boundary never has partial coverage to begin with, so it has
        // nothing to alias — it just hops a whole device pixel at a time.
        const double dpr = window() ? window()->effectiveDevicePixelRatio() : 1.0;
        const auto hf = static_cast<float>(h);
        const auto appendSnappedVLine = [&](double xCenter, double widthPx,
                quint8 R, quint8 G, quint8 B, quint8 A) {
            const double leftEdge = xCenter - widthPx / 2.0;
            const double snappedLeft = std::round(leftEdge * dpr) / dpr;
            const double widthDp = std::max(1.0, std::round(widthPx * dpr));
            const auto gx0 = static_cast<float>(snappedLeft);
            const auto gx1 = static_cast<float>(snappedLeft + widthDp / dpr);
            verts.append({gx0, 0.0f, R, G, B, A});
            verts.append({gx1, 0.0f, R, G, B, A});
            verts.append({gx0, hf, R, G, B, A});
            verts.append({gx1, 0.0f, R, G, B, A});
            verts.append({gx1, hf, R, G, B, A});
            verts.append({gx0, hf, R, G, B, A});
        };

        for (auto it = beginIt; it != endIt; ++it) {
            const double beatMs = *it;
            const double beatIndex = beatMs / msPerIndex;
            const double xCenter = centerX0 + (beatIndex - m_currentIndex) * m_pixelsPerSample;
            if (xCenter < -3.0 || xCenter > w + 3.0) continue;

            const auto rawIndex = static_cast<long long>(it - m_beatPositionsMs.begin());
            const bool isDownbeat = ((rawIndex % 4) - m_downbeatOffset + 4) % 4 == 0;
            const double widthPx = isDownbeat ? 2.2 : 1.2;
            const quint8 R = isDownbeat ? downbeatR : 255;
            const quint8 G = isDownbeat ? downbeatG : 255;
            const quint8 B = isDownbeat ? downbeatB : 255;
            const quint8 A = isDownbeat ? downbeatA : gridA;

            appendSnappedVLine(xCenter, widthPx, R, G, B, A);
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
