import SwiftUI

/// Add / edit sheet. Type options come from `GET /api/task-types`; status is
/// only editable for existing tasks (mirrors the backend's transition rules).
struct TaskEditorView: View {
    enum Mode {
        case create
        case edit(Task)
    }

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    let mode: Mode

    @State private var title = ""
    @State private var description = ""
    @State private var type = ""
    @State private var priority = 3
    @State private var assignedAgent = ""
    @State private var status = "pending"
    @State private var originalStatus = "pending"
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var didPopulate = false

    private var isEditing: Bool {
        if case .edit = mode { return true }
        return false
    }

    private var editingTask: Task? {
        if case .edit(let task) = mode { return task }
        return nil
    }

    private var typeOptions: [TaskType] {
        guard let editingTask else { return model.taskTypes }
        if model.taskTypes.contains(where: { $0.id == editingTask.type }) {
            return model.taskTypes
        }
        // Never lose the current type if the server no longer lists it.
        let fallback = TaskType(id: editingTask.type, name: editingTask.type, description: nil)
        return model.taskTypes + [fallback]
    }

    private var canSave: Bool {
        !isSaving
            && !model.isReadOnly
            && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !type.isEmpty
            && !typeOptions.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            formBody
            Divider()
            footer
        }
        .frame(minWidth: 520, idealWidth: 540, minHeight: 500, idealHeight: 560)
        .onAppear(perform: populate)
        .onChange(of: model.taskTypes) { _, _ in
            if type.isEmpty && !isEditing {
                type = model.taskTypes.first?.id ?? ""
            }
        }
        .task { await model.loadTaskTypesIfNeeded() }
    }

    // MARK: Populate

    private func populate() {
        guard !didPopulate else { return }
        didPopulate = true

        guard let task = editingTask else {
            type = model.taskTypes.first?.id ?? ""
            priority = 3
            return
        }

        title = task.title
        description = task.description
        type = task.type
        priority = task.priority
        assignedAgent = task.assignedAgent ?? ""
        status = task.status
        originalStatus = task.status
    }

    // MARK: Header / footer

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(isEditing ? "Edit Task" : "New Task")
                    .font(.title3.weight(.semibold))
                if let task = editingTask {
                    Text("#\(task.shortID) · \(TaskPresentation.statusTitle(task.status))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding(16)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            if let errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            } else if model.isReadOnly {
                Label("The server is read-only; saving is disabled.", systemImage: "lock")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            Spacer()

            Button("Cancel", role: .cancel) { dismiss() }
                .keyboardShortcut(.cancelAction)

            Button(isSaving ? "Saving…" : "Save") { save() }
                .keyboardShortcut(.defaultAction)
                .disabled(!canSave)
        }
        .padding(16)
    }

    // MARK: Form

    private var formBody: some View {
        Form {
            Section {
                TextField("Title", text: $title, axis: .vertical)
                    .lineLimit(1...2)

                Picker("Type", selection: $type) {
                    if typeOptions.isEmpty {
                        Text(model.taskTypesError == nil ? "Loading task types…" : "Task types unavailable")
                            .tag("")
                    }
                    ForEach(typeOptions) { option in
                        Text(option.name).tag(option.id)
                    }
                }
                .disabled(typeOptions.isEmpty)

                Picker("Priority", selection: $priority) {
                    ForEach(1...5, id: \.self) { value in
                        Text("P\(value) — \(TaskPresentation.priorityTitle(value))").tag(value)
                    }
                }

                if isEditing {
                    Picker("Status", selection: $status) {
                        ForEach(TaskPresentation.statuses, id: \.self) { value in
                            Text(TaskPresentation.statusTitle(value)).tag(value)
                        }
                    }
                    Text("Changing status uses the queue's normal transitions (hold/release/complete/fail).")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Description") {
                TextEditor(text: $description)
                    .font(.body)
                    .frame(minHeight: 110)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color.secondary.opacity(0.25))
                    )
            }

            Section("Assignment") {
                TextField("Assigned agent", text: $assignedAgent)
                Text("Leave empty for an unassigned task.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let taskTypesError = model.taskTypesError {
                Section {
                    Label(taskTypesError, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                    Button("Retry loading task types") {
                        AsyncTask<Void, Never> { @MainActor in
                            await model.loadTaskTypes(force: true)
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    // MARK: Save

    private func save() {
        guard canSave else { return }

        let draft = TaskDraft(
            title: title.trimmingCharacters(in: .whitespacesAndNewlines),
            description: description,
            type: type,
            priority: priority,
            assignedAgent: assignedAgent.trimmingCharacters(in: .whitespacesAndNewlines),
            status: isEditing && status != originalStatus ? status : nil
        )

        isSaving = true
        errorMessage = nil

        AsyncTask<Void, Never> { @MainActor in
            do {
                if let task = editingTask {
                    try await model.updateTask(task, from: draft)
                } else {
                    try await model.createTask(from: draft)
                }
                isSaving = false
                dismiss()
            } catch {
                isSaving = false
                errorMessage = error.localizedDescription
            }
        }
    }
}
