import SwiftUI
@main struct HelmRemoteApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(model).tint(HelmStyle.accent).preferredColorScheme(.dark)
                .onOpenURL { model.proposePairing($0) }
                .task(id: scenePhase) {
                    guard scenePhase == .active else { return }
                    while !Task.isCancelled { await model.refresh(); try? await Task.sleep(for: .seconds(5)) }
                }
                .alert("Pair with this bridge?", isPresented: Binding(get: { model.pairingProposal != nil }, set: { if !$0 { model.pairingProposal = nil } })) {
                    Button("Pair") {
                        guard let proposal = model.pairingProposal else { return }
                        Task { do { try await model.pair(proposal) } catch { model.error = error.localizedDescription } }
                        model.pairingProposal = nil
                    }
                    Button("Cancel", role: .cancel) { model.pairingProposal = nil }
                } message: { Text("Bridge: \(model.pairingProposal?.url.absoluteString ?? "")\nCertificate: \(model.pairingProposal?.certificateFingerprint ?? "System trust")\nCompare this with your computer. This key grants bridge controls, including enabled terminals.") }
        }
    }
}
