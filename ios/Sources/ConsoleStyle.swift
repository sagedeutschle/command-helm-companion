import SwiftUI

// Static instrument framing. No simulated readings, alarms or background animation.
enum HelmStyle {
    static let background = Color(red: 0.025, green: 0.045, blue: 0.055)
    static let surface = Color(red: 0.065, green: 0.095, blue: 0.11)
    static let raised = Color(red: 0.10, green: 0.14, blue: 0.155)
    static let accent = Color(red: 1.0, green: 0.32, blue: 0.34)
    static let gold = Color(red: 1.0, green: 0.72, blue: 0.33)
    static let signal = Color(red: 0.40, green: 0.87, blue: 0.80)
    static let rule = Color(red: 0.27, green: 0.37, blue: 0.40)
    static func color(_ state: String) -> Color {
        switch state {
        case "waiting", "pending", "queued", "interrupted", "offline", "stale": return gold
        case "working", "running": return accent
        case "completed", "finished", "delivered", "online", "ready": return signal
        case "failed": return accent
        default: return .secondary
        }
    }
    static func label(_ state: String) -> String {
        switch state {
        case "working": return "In progress"
        case "waiting": return "Needs you"
        case "finished": return "Finished"
        case "stale": return "No recent signal"
        case "offline": return "Offline"
        case "quiet": return "No projects"
        default: return state.capitalized
        }
    }
}

struct HullPlate: Shape {
    func path(in rect: CGRect) -> Path {
        let cut: CGFloat = min(10, min(rect.width, rect.height) / 4)
        var p = Path()
        p.move(to: CGPoint(x: rect.minX + cut, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - cut))
        p.addLine(to: CGPoint(x: rect.maxX - cut, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.minY + cut))
        p.closeSubpath()
        return p
    }
}

struct ConsoleBackground: View {
    var body: some View {
        ZStack {
            HelmStyle.background
            Canvas { context, size in
                var ribs = Path()
                for x in stride(from: CGFloat(18), to: size.width, by: CGFloat(96)) {
                    ribs.move(to: CGPoint(x: x, y: 0))
                    ribs.addLine(to: CGPoint(x: x, y: size.height))
                }
                for y in stride(from: CGFloat(0), to: size.height, by: CGFloat(96)) {
                    ribs.move(to: CGPoint(x: 0, y: y))
                    ribs.addLine(to: CGPoint(x: size.width, y: y))
                }
                context.stroke(ribs, with: .color(HelmStyle.rule.opacity(0.14)), lineWidth: 0.5)
            }
            LinearGradient(colors: [HelmStyle.accent.opacity(0.045), .clear, .black.opacity(0.18)], startPoint: .topLeading, endPoint: .bottomTrailing)
        }.ignoresSafeArea().allowsHitTesting(false).accessibilityHidden(true)
    }
}

struct Panel<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(LinearGradient(colors: [HelmStyle.raised, HelmStyle.surface], startPoint: .topLeading, endPoint: .bottomTrailing), in: HullPlate())
            .overlay(HullPlate().stroke(HelmStyle.rule.opacity(0.65), lineWidth: 0.75).allowsHitTesting(false))
            .overlay(alignment: .topLeading) {
                Rectangle().fill(HelmStyle.accent).frame(width: 32, height: 2).padding(.leading, 18).accessibilityHidden(true).allowsHitTesting(false)
            }
            .overlay(alignment: .bottomTrailing) {
                HStack(spacing: 3) { ForEach(0..<3) { _ in Rectangle().fill(HelmStyle.rule).frame(width: 2, height: 5) } }
                    .padding(.trailing, 18).padding(.bottom, 5).accessibilityHidden(true).allowsHitTesting(false)
            }
    }
}

struct ConsoleHeading: View {
    let title: String
    let icon: String
    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: icon).foregroundStyle(HelmStyle.accent)
            Text(title.uppercased()).font(.caption.monospaced().weight(.semibold)).tracking(1.6)
            Rectangle().fill(HelmStyle.rule).frame(height: 1).accessibilityHidden(true)
        }.foregroundStyle(.secondary).padding(.vertical, 3).accessibilityAddTraits(.isHeader)
    }
}

struct ConsoleMetric: View {
    let title: String
    let value: Int
    let color: Color
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption2.monospaced().weight(.semibold)).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(value.formatted()).font(.title2.monospaced().weight(.semibold)).monospacedDigit().foregroundStyle(color)
                Spacer(minLength: 0)
            }
            Rectangle().fill(color.opacity(0.65)).frame(height: 2).accessibilityHidden(true)
        }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(HelmStyle.surface, in: RoundedRectangle(cornerRadius: 3))
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(HelmStyle.rule.opacity(0.55), lineWidth: 0.5))
            .accessibilityElement(children: .ignore).accessibilityLabel("\(title), \(value)")
    }
}

struct StateTag: View {
    let state: String
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(HelmStyle.color(state)).frame(width: 5, height: 5).accessibilityHidden(true)
            Text(HelmStyle.label(state)).font(.caption.monospaced().weight(.medium))
        }.foregroundStyle(HelmStyle.color(state)).padding(.horizontal, 8).padding(.vertical, 5)
            .background(HelmStyle.color(state).opacity(0.08), in: RoundedRectangle(cornerRadius: 3))
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(HelmStyle.color(state).opacity(0.25), lineWidth: 0.5))
    }
}

struct ConsoleScreen: ViewModifier {
    func body(content: Content) -> some View {
        content.scrollContentBackground(.hidden).background(ConsoleBackground())
            .toolbarBackground(HelmStyle.surface, for: .navigationBar, .tabBar)
            .toolbarBackground(.visible, for: .navigationBar, .tabBar)
    }
}
extension View {
    func helmConsole() -> some View { modifier(ConsoleScreen()) }
}

struct DesktopDownloadPanel: View {
    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 10) {
                ConsoleHeading(title: "Desktop companion", icon: "desktopcomputer")
                Link(destination: PublicHelp.desktop) {
                    HStack {
                        Text("Download Command Helm Desktop").font(.subheadline.weight(.semibold))
                        Spacer(minLength: 8)
                        Image(systemName: "arrow.up.right")
                    }.frame(minHeight: 44).contentShape(Rectangle())
                }.accessibilityIdentifier("downloadDesktop")
                Text("Mac preview · Apple Silicon. Install on your Mac, then connect both apps to your bridge.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }
}
