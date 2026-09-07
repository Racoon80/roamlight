//  Uploading: pick photographs out of the camera roll and walk the server's
//  steps (batch -> file -> chunk -> done -> commit).
//
//  ⚠ This is **exactly** the path the web form takes. No second way into the
//    server, no shortcut for the app -- otherwise there would be two places
//    where a virus scan or a duplicate check could be forgotten.
//
//  ⚠ And, like the web form, it is "pick one or type a new one". An album is
//    created by naming one that does not exist yet -- so the existing ones
//    have to be visible, or the only way to add to last year's album is to
//    remember precisely how it was spelled.

import PhotosUI
import SwiftUI

struct UploadView: View {
    @EnvironmentObject var state: AppState

    @State private var picked: [PhotosPickerItem] = []
    @State private var year = String(Calendar.current.component(.year, from: .now))
    @State private var event = ""
    @State private var country = ""
    @State private var place = ""

    /// What already exists, for picking. Loaded once when the view appears; a
    /// failure here must never block an upload, so it stays empty and the
    /// fields simply work as plain text.
    @State private var albums: [Album] = []
    @State private var places: [String] = []

    @State private var running = false
    @State private var done = 0
    @State private var total = 0
    @State private var note: String?
    @State private var failed: String?

    private var ready: Bool {
        !picked.isEmpty && !trimmed(year).isEmpty && !trimmed(event).isEmpty
            && !trimmed(country).isEmpty && !running
    }

    private func trimmed(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The album the four fields point at, if it is one that already exists.
    /// ⚠ Compared on the same key the server uses (`<year>/<country>/<name>`),
    ///   so "new" here means exactly what "new" means over there.
    private var existing: Album? {
        albums.first { $0.year == trimmed(year) && $0.country == trimmed(country)
                       && $0.event == trimmed(event) }
    }

    private var isNewAlbum: Bool {
        !trimmed(year).isEmpty && !trimmed(country).isEmpty && !trimmed(event).isEmpty
            && existing == nil
    }

    private var years: [String] {
        var out = Set(albums.map(\.year))
        out.insert(String(Calendar.current.component(.year, from: .now)))
        return out.sorted(by: >)
    }

    private var countries: [String] {
        Set(albums.map(\.country)).sorted()
    }

    var body: some View {
        Form {
            Section("Photographs") {
                PhotosPicker(selection: $picked, matching: .any(of: [.images, .videos]),
                             photoLibrary: .shared()) {
                    Label(picked.isEmpty ? "Pick photographs"
                                         : "\(picked.count) picked", systemImage: "photo.stack")
                }
            }

            Section {
                // Pick an album that is already there -- one tap fills all
                // four fields, the same as choosing one in the web form.
                Menu {
                    Button {
                        year = String(Calendar.current.component(.year, from: .now))
                        country = ""
                        event = ""
                        place = ""
                    } label: {
                        Label("New album…", systemImage: "plus")
                    }
                    if !albums.isEmpty {
                        Divider()
                        ForEach(albums) { a in
                            Button {
                                year = a.year
                                country = a.country
                                event = a.event
                            } label: {
                                Text("\(a.title) · \(a.country) · \(a.n)")
                            }
                        }
                    }
                } label: {
                    HStack {
                        Label(existing.map { "\($0.title)" } ?? "Choose an album",
                              systemImage: "square.grid.2x2")
                        Spacer()
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.footnote).foregroundStyle(Theme.inkSoft)
                    }
                }
            } header: {
                Text("Album")
            } footer: {
                Text(albums.isEmpty
                     ? "Nothing here yet — type the four fields below and it is created."
                     : "Pick one, or fill in the fields below to make a new one.")
            }

            Section {
                // ⚠ The same four fields as on the site, in the same order:
                //   year, name, country, place. The server lays out the folder.
                comboField("Year", text: $year, options: years)
                    .keyboardType(.numberPad)
                TextField("Name of the album", text: $event)
                comboField("Country", text: $country, options: countries)
                comboField("Place / town (for the map)", text: $place, options: places)
            } header: {
                Text("Where they belong")
            } footer: {
                if isNewAlbum {
                    Label("New album: \(trimmed(year)) / \(trimmed(country)) / \(trimmed(event))",
                          systemImage: "plus.circle")
                } else if let existing {
                    Label("Adding to \(existing.title) (\(existing.n) already there)",
                          systemImage: "checkmark.circle")
                } else {
                    Text("Year, country and name decide where the photographs go.")
                }
            }

            Section {
                Button {
                    Task { await send() }
                } label: {
                    if running {
                        HStack {
                            ProgressView()
                            Text("\(done) of \(total)…")
                        }
                    } else {
                        Text(isNewAlbum ? "Create album and upload" : "Upload")
                    }
                }
                .disabled(!ready)

                if let note { Text(note).font(.footnote).foregroundStyle(Theme.inkSoft) }
                if let failed { Text(failed).font(.footnote).foregroundStyle(.red) }
            }
        }
        .navigationTitle("Upload")
        .scrollContentBackground(.hidden)
        .background(Theme.ground)
        .task { await load() }
    }

    /// A text field you can also pick from. ⚠ Typing stays possible on every
    /// one of them -- that IS how a new album is made.
    @ViewBuilder
    private func comboField(_ label: String, text: Binding<String>,
                            options: [String]) -> some View {
        HStack {
            TextField(label, text: text)
            if !options.isEmpty {
                Menu {
                    ForEach(options, id: \.self) { o in
                        Button(o) { text.wrappedValue = o }
                    }
                } label: {
                    Image(systemName: "chevron.down")
                        .font(.footnote).foregroundStyle(Theme.inkSoft)
                }
            }
        }
    }

    /// ⚠ Swallows its errors on purpose: these lists are a convenience. If they
    ///   cannot be fetched, the four fields still work and an upload still goes
    ///   through -- an empty picker must not become a locked form.
    private func load() async {
        guard albums.isEmpty else { return }
        if let a = try? await state.api.albums() { albums = a }
        if let f = try? await state.api.facets() { places = f.places }
    }

    private func send() async {
        running = true
        failed = nil
        note = nil
        done = 0
        total = picked.count
        let wasNew = isNewAlbum
        defer { running = false }

        let api = state.api
        do {
            let batch = try await api.newBatch()
            for (i, item) in picked.enumerated() {
                guard let data = try await item.loadTransferable(type: Data.self) else { continue }
                // ⚠ There HAS to be a name: the server hangs the extension off
                //   it and tells from that what kind of file it is.
                let name = (item.supportedContentTypes.first?.preferredFilenameExtension)
                    .map { "IMG_\(i + 1).\($0)" } ?? "IMG_\(i + 1).jpg"
                let fid = try await api.addFile(batch: batch, name: name, size: data.count)

                var offset = 0
                while offset < data.count {
                    let end = min(offset + API.chunk, data.count)
                    try await api.sendChunk(batch: batch, file: fid, offset: offset,
                                            data: data.subdata(in: offset..<end))
                    offset = end
                }
                try await api.finishFile(batch: batch, file: fid)
                done += 1
            }
            try await api.commit(batch: batch, year: trimmed(year), country: trimmed(country),
                                 event: trimmed(event), place: trimmed(place))
            note = "\(done) photograph\(done == 1 ? "" : "s") sent"
                + (wasNew ? " into the new album “\(trimmed(event))”." : ".")
                + " The site is converting them now."
            picked = []
            // The new album has to turn up in the picker straight away --
            // otherwise the next upload would look like it has to be created
            // a second time.
            if let a = try? await api.albums() { albums = a }
        } catch {
            failed = error.localizedDescription
        }
    }
}
