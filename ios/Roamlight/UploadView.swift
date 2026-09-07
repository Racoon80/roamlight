//  En neien Album uleeën.
//
//  ⚠ This tab used to be a general upload form, with a picker for choosing an
//    album you already have. That was one thing done twice: adding to an album
//    that exists belongs INSIDE that album, where you are already looking at
//    it, and that is where the `+` in the toolbar does it. What was missing was
//    the other half — making a new album at all — and there was no way to do it
//    from the app.
//
//  ⚠ The server does not have a "create album" call, and it does not need one:
//    an album IS the year, the country and the name. Name three that do not
//    exist yet, send photographs with them, and the album exists. That is
//    exactly what the web form does.

import PhotosUI
import SwiftUI

struct UploadView: View {
    @EnvironmentObject var state: AppState

    @State private var name = ""
    @State private var year = String(Calendar.current.component(.year, from: .now))
    @State private var country = ""
    @State private var place = ""
    @State private var picked: [PhotosPickerItem] = []

    /// What already exists — for the two pickers, and to tell whether this
    /// album is really new.
    @State private var albums: [Album] = []
    @State private var places: [String] = []

    @State private var running = false
    @State private var done = 0
    @State private var made: Album?
    @State private var note: String?
    @State private var failed: String?

    private func trimmed(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// ⚠ Compared on the same key the server uses (`<year>/<country>/<name>`),
    ///   so "already there" here means exactly what it means over there.
    private var existing: Album? {
        albums.first { $0.year == trimmed(year) && $0.country == trimmed(country)
                       && $0.event == trimmed(name) }
    }

    private var named: Bool {
        !trimmed(name).isEmpty && !trimmed(year).isEmpty && !trimmed(country).isEmpty
    }

    private var ready: Bool { named && !picked.isEmpty && !running }

    private var years: [String] {
        let now = String(Calendar.current.component(.year, from: .now))
        return Set(albums.map(\.year) + [now]).sorted(by: >)
    }

    private var countries: [String] { Set(albums.map(\.country)).sorted() }

    var body: some View {
        Form {
            Section {
                TextField("Name of the album", text: $name)
                comboField("Year", text: $year, options: years)
                    .keyboardType(.numberPad)
                comboField("Country", text: $country, options: countries)
                comboField("Place / town (for the map)", text: $place, options: places)
            } header: {
                Text("The album")
            } footer: {
                // ⚠ Say the truth about what will happen. Naming an album that
                //   is already there does NOT fail and does not make a second
                //   one — the photographs simply go into the one that exists.
                //   Letting somebody find that out afterwards would be a
                //   surprise; saying it here is a choice.
                if let existing {
                    Label("\(existing.title) already exists — these will go into it, "
                          + "next to the \(existing.n) already there.",
                          systemImage: "info.circle")
                } else if named {
                    Label("New album: \(trimmed(year)) / \(trimmed(country)) / \(trimmed(name))",
                          systemImage: "plus.circle")
                } else {
                    Text("The year, the country and the name are what make an album.")
                }
            }

            Section("Photographs") {
                PhotosPicker(selection: $picked,
                             matching: .any(of: [.images, .videos]),
                             photoLibrary: .shared()) {
                    Label(picked.isEmpty ? "Pick photographs"
                                         : "\(picked.count) picked",
                          systemImage: "photo.stack")
                }
            }

            Section {
                Button {
                    Task { await send() }
                } label: {
                    if running {
                        HStack {
                            ProgressView()
                            Text("\(done) of \(picked.count)…")
                        }
                    } else {
                        Text(existing == nil ? "Create the album" : "Add to the album")
                    }
                }
                .disabled(!ready)

                if let note { Text(note).font(.footnote).foregroundStyle(Theme.inkSoft) }
                if let failed { Text(failed).font(.footnote).foregroundStyle(.red) }
                if let made {
                    NavigationLink(value: made) {
                        Label("Open \(made.title)", systemImage: "arrow.right.circle")
                    }
                }
            }
        }
        .navigationTitle("New album")
        .navigationDestination(for: Album.self) { PhotosView(album: $0) }
        .scrollContentBackground(.hidden)
        .background(Theme.ground)
        .task { await load() }
    }

    /// A text field you can also pick from. ⚠ Typing stays possible on every
    /// one of them — that IS how a new album is made.
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
    ///   cannot be fetched, the fields still work and an album can still be
    ///   made — an empty picker must not become a locked form.
    private func load() async {
        if let a = try? await state.api.albums() { albums = a }
        if let f = try? await state.api.facets() { places = f.places }
    }

    private func send() async {
        running = true
        failed = nil
        note = nil
        made = nil
        done = 0
        let wasNew = existing == nil
        defer { running = false }

        let api = state.api
        do {
            let batch = try await api.newBatch()
            for (i, item) in picked.enumerated() {
                guard let data = try await item.loadTransferable(type: Data.self) else { continue }
                // ⚠ There HAS to be a name: the server hangs the extension off
                //   it and tells from that what kind of file it is.
                let ext = item.supportedContentTypes.first?.preferredFilenameExtension ?? "jpg"
                let fid = try await api.addFile(batch: batch, name: "IMG_\(i + 1).\(ext)",
                                                size: data.count)
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
                                 event: trimmed(name), place: trimmed(place))

            note = "\(done) photograph\(done == 1 ? "" : "s") sent"
                + (wasNew ? " — the album is being made." : " — the site is converting them.")
            picked = []

            // ⚠ The album is only really there once the site has converted the
            //   first photograph: until then it is a folder with nothing on the
            //   site in it, and it does not appear in the list. So wait for it,
            //   and then offer to open it — otherwise "Create the album" ends
            //   with nothing to show for it.
            for _ in 0..<20 {
                if let a = try? await api.albums() {
                    albums = a
                    if let found = a.first(where: {
                        $0.year == trimmed(year) && $0.country == trimmed(country)
                            && $0.event == trimmed(name) }) {
                        made = found
                        note = "\(done) photograph\(done == 1 ? "" : "s") in "
                             + "\(found.title)."
                        break
                    }
                }
                try? await Task.sleep(for: .seconds(1.5))
            }
            if made == nil {
                note = "\(done) sent. The album appears once the site has converted them."
            }
        } catch {
            failed = error.localizedDescription
        }
    }
}
