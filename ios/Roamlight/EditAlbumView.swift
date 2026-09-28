//  Edit album: name, place, the journey, who sees it -- and deleting it.
//
//  ⚠ The same routes the website's workshop uses, and the same rule: an admin
//    any album, anybody else only an album with nothing but their own
//    photographs in it. The server says so when the screen opens
//    (`/api/albums/settings` answers 403 otherwise) and again on every save.

import SwiftUI

struct EditAlbumView: View {
    let album: Album
    /// `true` = the album is gone or has a new name -- the screen behind has to
    /// go back to the list, because the album it shows no longer exists there.
    var onDone: (_ left: Bool) -> Void

    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var settings: AlbumSettings?
    @State private var failed: String?
    @State private var saving = false
    @State private var askDelete = false

    @State private var year = ""
    @State private var country = ""
    @State private var event = ""
    @State private var place = ""
    @State private var departure = ""
    @State private var transport = "car"
    @State private var audience: Set<String> = []

    var body: some View {
        NavigationStack {
            Group {
                if let s = settings {
                    form(s)
                } else if let failed {
                    Text(failed).foregroundStyle(.red).padding()
                } else {
                    ProgressView().tint(Theme.safelight)
                }
            }
            .navigationTitle("Edit album")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if saving { ProgressView() } else {
                        Button("Save") { Task { await save() } }
                            .disabled(settings == nil || !valid)
                    }
                }
            }
        }
        .task { await load() }
        .confirmAlbumRemoval(Binding(get: { askDelete ? album : nil },
                                     set: { if $0 == nil { askDelete = false } })) { a in
            await remove(a)
        }
    }

    // MARK: - D'Form

    private func form(_ s: AlbumSettings) -> some View {
        Form {
            Section("Album") {
                TextField("Name", text: $event)
                TextField("Year", text: $year).keyboardType(.numberPad)
                TextField("Country", text: $country)
            }

            // ⚠ The place IS the destination of the journey when the
            //   photographs carry no GPS -- the site looks it up. That is why
            //   it sits here and not with the name.
            Section {
                TextField("Departure (empty = from home)", text: $departure)
                Picker("By", selection: $transport) {
                    ForEach(s.transports, id: \.self) { Text(label($0)).tag($0) }
                }
                TextField("Destination (place)", text: $place)
            } header: {
                Text("Journey")
            } footer: {
                if s.journey.multi && !s.journey.legs.isEmpty {
                    Text("This album has \(s.journey.legs.count) stops. They are kept; "
                         + "change them on the website.")
                }
            }

            Section {
                if s.people.isEmpty && s.groups.isEmpty {
                    Text("Nobody to choose from yet.").foregroundStyle(Theme.inkMute)
                }
                ForEach(s.groups) { c in toggle(c, symbol: "person.3") }
                ForEach(s.people) { c in toggle(c, symbol: "person") }
            } header: {
                Text("Who sees it")
            } footer: {
                Text(audience.isEmpty
                     ? "Nobody ticked: only the administrators see this album."
                     : "Administrators always see every album.")
            }

            Section {
                Button(role: .destructive) { askDelete = true } label: {
                    Label("Delete album", systemImage: "trash")
                }
            }

            if let failed {
                Section { Text(failed).foregroundStyle(.red) }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.ground)
        .disabled(saving)
    }

    private func toggle(_ c: AlbumSettings.Choice, symbol: String) -> some View {
        Toggle(isOn: Binding(
            get: { audience.contains(c.principal) },
            set: { on in if on { audience.insert(c.principal) } else { audience.remove(c.principal) } }
        )) {
            Label(c.name, systemImage: symbol)
        }
    }

    private func label(_ mode: String) -> String {
        ["car": "Car", "bus": "Bus", "train": "Train", "plane": "Plane"][mode] ?? mode.capitalized
    }

    private var valid: Bool {
        year.count == 4 && Int(year) != nil
            && !country.trimmingCharacters(in: .whitespaces).isEmpty
            && !event.trimmingCharacters(in: .whitespaces).isEmpty
    }

    // MARK: - Lueden a späicheren

    private func load() async {
        do {
            let s = try await state.api.albumSettings(album)
            year = s.year; country = s.country; event = s.event; place = s.place
            departure = s.journey.departure; transport = s.journey.transport
            audience = Set(s.audience)
            settings = s
        } catch APIError.http(403, _) {
            failed = "Only an administrator, or whoever uploaded every photograph "
                + "in it, can edit this album."
        } catch {
            failed = error.localizedDescription
        }
    }

    /// ⚠ In this order: who sees it and the journey FIRST, the name LAST. Both
    ///   are keyed by the album's name; renamed first, they would be written
    ///   for a key that no longer exists.
    private func save() async {
        guard let s = settings else { return }
        saving = true
        failed = nil
        defer { saving = false }
        do {
            if audience != Set(s.audience) {
                try await state.api.setAudience(album, audience.sorted())
            }
            let dep = departure.trimmingCharacters(in: .whitespaces)
            if dep != s.journey.departure || transport != s.journey.transport {
                try await state.api.setJourney(album, departure: dep, transport: transport,
                                               form: s.journey)
            }
            let trimmed = (year.trimmingCharacters(in: .whitespaces),
                           country.trimmingCharacters(in: .whitespaces),
                           event.trimmingCharacters(in: .whitespaces),
                           place.trimmingCharacters(in: .whitespaces))
            let renamed = trimmed.0 != s.year || trimmed.1 != s.country || trimmed.2 != s.event
            if renamed || trimmed.3 != s.place {
                _ = try await state.api.editAlbum(album, year: trimmed.0, country: trimmed.1,
                                                  event: trimmed.2, place: trimmed.3)
            }
            dismiss()
            onDone(renamed)
        } catch {
            failed = error.localizedDescription
        }
    }

    private func remove(_ a: Album) async {
        saving = true
        defer { saving = false }
        do {
            let r = try await state.api.removeAlbum(a)
            if r.failed.isEmpty {
                dismiss()
                onDone(true)
            } else {
                failed = "\(r.failed.count) could not be deleted."
            }
        } catch {
            failed = error.localizedDescription
        }
    }
}
