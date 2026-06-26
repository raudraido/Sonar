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
        const double val = rawVal * rawVal * 0.5 + rawVal * 0.5;
        const double barH = val * maxBarH0;

        const double brightness = std::clamp(
                isLeft ? (120.0 + 135.0 * fade) : (70.0 + 130.0 * fade), 0.0, 255.0);
        const double hueShift = std::floor(20.0 * rawVal);
        const double finalHue = std::fmod(std::fmod(baseHue + hueShift, 360.0) + 360.0, 360.0);
        const double saturation = std::clamp(255.0 * std::max(0.3, 1.0 - rawVal * 0.6), 0.0, 255.0);

        const Rgb rgb = hsvToRgb(finalHue, saturation, brightness);
        const auto R = static_cast<quint8>(std::round(rgb.r * 255.0));
        const auto G = static_cast<quint8>(std::round(rgb.g * 255.0));
        const auto B = static_cast<quint8>(std::round(rgb.b * 255.0));
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
