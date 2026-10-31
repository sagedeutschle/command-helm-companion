import SwiftUI

struct TasksView: View {
    @EnvironmentObject var model: AppModel
    @State private var submissionError: String?
    @State private var showAIConsent = false
    var tasks: [HelmTask] { model.taskSnapshot.tasks.sorted {
        let active: Set<String> = ["running", "queued"]
        if active.contains($0.state) != active.contains($1.state) { return active.contains($0.state) }
        return $0.createdAt > $1.createdAt
    } }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                taskConnection
                VStack(alignment: .leading, spacing: 8) {
                    Text("COMMAND HELM").font(.caption.monospaced()).tracking(3).foregroundStyle(HelmStyle.accent)
                    Text("Work, sent home.").font(.largeTitle.weight(.semibold))
                    Text("Give Codex a task. Choose the computer where the work and report should arrive.").foregroundStyle(.secondary)
                }
                newTask
                if let notice = model.taskNotice { Text(notice).font(.footnote).foregroundStyle(HelmStyle.gold).textSelection(.enabled) }
                if let submissionError { Label(submissionError, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(HelmStyle.gold) }
                if let error = model.taskError { Label(error, systemImage: "wifi.exclamationmark").font(.footnote).foregroundStyle(HelmStyle.gold) }
                workers
                HStack { Text("Task history").font(.title2.weight(.semibold)); Spacer(); if model.taskRefreshing { ProgressView() } }
                if tasks.isEmpty {
                    EmptyPanel(title: "Your next task starts here", text: "Saved tasks stay in the queue until the selected computer can run them. Reports and delivery folders appear here.", icon: "checklist")
                }
                ForEach(tasks) { task in
                    NavigationLink { TaskDetailView(task: task) } label: {
                        Panel {
                            VStack(alignment: .leading, spacing: 12) {
                                HStack { Text(model.deviceName(task.target)).font(.caption).foregroundStyle(.secondary); Spacer(); StateTag(state: task.state) }
                                Text(task.prompt).font(.headline).lineLimit(3).multilineTextAlignment(.leading).foregroundStyle(.primary)
                                HStack { Text(Date(timeIntervalSince1970: task.createdAt), style: .date); Spacer(); Image(systemName: "chevron.right") }.font(.caption).foregroundStyle(.secondary)
                                if !task.deliveryPath.isEmpty { Label(task.deliveryPath, systemImage: "folder").font(.caption.monospaced()).lineLimit(2).foregroundStyle(.secondary) }
                            }
                        }
                    }.buttonStyle(.plain)
                }
            }.padding(20).frame(maxWidth: 760)
        }.background(HelmStyle.background).navigationTitle("Tasks").navigationBarTitleDisplayMode(.inline)
            .task { await model.refreshTasks(); await model.refreshDevices() }
            .sheet(isPresented: $showAIConsent) { AIConsentView(deviceName: model.deviceName(model.taskDraft.target)) { model.allowAI(model.taskDraft.target.rawValue); showAIConsent = false; send() } }
            .onChange(of: model.taskDraft.id) { _, _ in submissionError = nil }
            .refreshable { await model.refreshTasks() }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { Task { await model.refreshTasks() } } label: { Image(systemName: "arrow.clockwise") }.accessibilityLabel("Refresh tasks") } }
    }
    private func send() { Task { do { try await model.submitTask() } catch { submissionError = error.localizedDescription } } }
    private var taskConnection: some View {
        HStack(spacing: 8) {
            Circle().fill(!model.tasksStale && model.taskError == nil ? HelmStyle.accent : HelmStyle.gold).frame(width: 6, height: 6)
            Text(model.demo ? "Demo · sample tasks only" : !model.tasksStale && model.taskError == nil ? "Task queue connected" : "Task queue needs a refresh")
            Spacer()
            if let date = model.taskLastSuccess { Text(date, style: .relative).monospacedDigit() }
        }.font(.caption).foregroundStyle(.secondary)
    }
    private var newTask: some View {
        Panel {
            VStack(alignment: .leading, spacing: 16) {
                Text("New Codex task").font(.title3.weight(.semibold))
                Picker("Deliver to", selection: $model.taskDraft.target) {
                    ForEach(model.taskTargets) { target in Text(model.deviceName(target)).tag(target) }
                }.pickerStyle(.menu).accessibilityIdentifier("taskTarget")
                if model.taskTargets.isEmpty { Text("Enable a task worker on a configured computer to send work. Terminal access alone does not run queued tasks.").font(.footnote).foregroundStyle(HelmStyle.gold) }
                TextField("What should Codex complete?", text: $model.taskDraft.prompt, axis: .vertical)
                    .lineLimit(4...10).accessibilityIdentifier("taskPrompt")
                Divider()
                TextField("Project folder (optional, absolute path)", text: $model.taskDraft.workspace)
                    .font(.callout.monospaced()).autocorrectionDisabled().textInputAutocapitalization(.never).accessibilityIdentifier("taskWorkspace")
                Text("Leave blank to use the worker’s configured default: a delivery folder on older workers, or its configured project folder on newer workers. Reports have a separate delivery folder shown in task history. An absolute path selects an existing project on that computer.").font(.footnote).foregroundStyle(.secondary)
                if let validation = model.taskDraft.validationMessage, !model.taskDraft.prompt.isEmpty || !model.taskDraft.workspace.isEmpty {
                    Text(validation).font(.caption).foregroundStyle(HelmStyle.gold)
                }
                Button {
                    submissionError = nil
                    if model.hasAIConsent(model.taskDraft.target.rawValue) { send() }
                    else { showAIConsent = true }
                } label: {
                    HStack { Text(model.taskSending ? "Saving task…" : "Send task"); Spacer(); Image(systemName: "paperplane") }.padding(8)
                }.buttonStyle(.borderedProminent).disabled(!model.canSubmitTask || model.taskDraft.validationMessage != nil || !model.taskTargets.contains(model.taskDraft.target)).accessibilityIdentifier("sendTask")
                if model.demo { Text("Demo mode cannot send tasks.").font(.caption).foregroundStyle(HelmStyle.gold) }
            }.disabled(model.taskSending || model.demo)
        }
    }
    private var workers: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(model.taskTargets) { target in
                let worker = model.taskSnapshot.workers.first { $0.target == target }
                let recent = !model.tasksStale && model.taskError == nil && worker?.isRecent() == true
                HStack {
                    Image(systemName: "desktopcomputer").foregroundStyle(HelmStyle.accent)
                    Text(model.deviceName(target)).font(.subheadline)
                    Spacer()
                    Text(model.demo ? "Sample" : recent ? worker?.state == "working" ? "Worker busy" : "Worker ready" : "No recent worker signal").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}
struct TaskDetailView: View {
    @EnvironmentObject var model: AppModel
    let task: HelmTask
    var current: HelmTask { model.taskSnapshot.tasks.first { $0.id == task.id } ?? task }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack { Text(model.deviceName(current.target)).font(.headline); Spacer(); StateTag(state: current.state) }
                if model.demo { Label("Sample task · no real delivery", systemImage: "eye").foregroundStyle(HelmStyle.gold) }
                if model.tasksStale || model.taskError != nil { Label("Last received state. Refresh to check progress.", systemImage: "wifi.exclamationmark").font(.footnote).foregroundStyle(HelmStyle.gold) }
                Text(current.prompt).font(.title3).textSelection(.enabled)
                if current.state == "queued" { Text("Waiting for the selected computer to claim this task. Sleeping or unavailable computers leave it queued.").font(.footnote).foregroundStyle(.secondary) }
                if current.state == "interrupted" { Text("This task stopped before completion. It will not restart automatically.").font(.footnote).foregroundStyle(HelmStyle.gold) }
                Panel {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Delivery folder").font(.headline)
                        Text(current.deliveryPath.isEmpty ? "Assigned when the worker starts." : current.deliveryPath).font(.callout.monospaced()).textSelection(.enabled)
                        if !current.workspace.isEmpty { Text("Project: \(current.workspace)").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                    }
                }
                if !current.error.isEmpty { Label(current.error, systemImage: "exclamationmark.triangle").foregroundStyle(HelmStyle.gold).textSelection(.enabled) }
                Panel {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Report").font(.headline)
                        Text(current.report.isEmpty ? "The worker’s report will appear here when it is available." : current.report).font(.callout).textSelection(.enabled)
                    }
                }
                Text("Updated \(Date(timeIntervalSince1970: current.updatedAt).formatted())").font(.caption).foregroundStyle(.secondary)
                Text("Task \(current.id)").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            }.padding(20).frame(maxWidth: 760)
        }.background(HelmStyle.background).navigationTitle("Task details").navigationBarTitleDisplayMode(.inline)
            .refreshable { await model.refreshTasks() }
    }
}
