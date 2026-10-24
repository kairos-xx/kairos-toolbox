#!/usr/bin/env node
 // Deterministically assemble the source ZIP and VSIX release artifacts.
import {
	deflateRawSync
} from "node:zlib";
import {
	mkdirSync,
	readdirSync,
	readFileSync,
	statSync,
	writeFileSync
} from "node:fs";
import {
	basename,
	dirname,
	join,
	resolve
} from "node:path";
import {
	fileURLToPath
} from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = resolve(root, "..", "vsix");
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const NAME = manifest.name;
const VERSION = manifest.version;
/** @type {number[]} */
const TABLE = (() => {
	const t = [];
	for (let n = 0; n < 256; n++) {
		let c = n;
		for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ c >>> 1 : c >>> 1;
		t[n] = c >>> 0;
	}
	return t;
})();
/**
 * Compute the CRC-32 checksum of a buffer.
 *
 * @param {Uint8Array} buf Data to checksum.
 * @returns {number} Unsigned CRC-32 value.
 */
const crc32 = buf => {
	let c = 0xFFFFFFFF;
	for (const b of buf) c = /** @type {number} */ (TABLE[(c ^ b) & 0xFF]) ^ c >>> 8;
	return (c ^ 0xFFFFFFFF) >>> 0;
};
/**
 * Collect the source payload recursively.
 *
 * @param {string} base Source root.
 * @param {string} prefix Archive path prefix.
 * @returns {{name: string, data: Buffer}[]} Sorted file entries.
 */
const collect = (base, prefix = "") => {
	/** @type {{name: string, data: Buffer}[]} */
	const files = [];
	/**
	 * Walk one directory into the file list.
	 *
	 * @param {string} dir Directory to walk.
	 * @returns {void}
	 */
	const walk = dir => {
		for (const entry of readdirSync(dir).sort()) {
			const path = join(dir, entry);
			if (entry === "node_modules" || entry.startsWith(".")) continue;
			if (statSync(path).isDirectory()) walk(path);
			else files.push({
				name: prefix + path.slice(base.length + 1).replaceAll("\\", "/"),
				data: readFileSync(path)
			});
		}
	};
	walk(base);
	return files;
};
/**
 * Assemble a deterministic ZIP archive.
 *
 * @param {{name: string, data: Buffer}[]} files Archive entries.
 * @returns {Buffer} The ZIP archive.
 */
const zip = files => {
	/** @type {Buffer[]} */
	const chunks = [];
	/** @type {Buffer[]} */
	const central = [];
	let offset = 0;
	for (const {
			name,
			data
		}
		of files.sort((a, b) => a.name < b.name ? -1 : 1)) {
		const compressed = deflateRawSync(data, {
			level: 9
		});
		const useStore = compressed.length >= data.length;
		const body = useStore ? data : compressed;
		const method = useStore ? 0 : 8;
		const nameBuf = Buffer.from(name, "utf8");
		const header = Buffer.alloc(30);
		header.writeUInt32LE(0x04034b50, 0);
		header.writeUInt16LE(20, 4);
		header.writeUInt16LE(0x0800, 6);
		header.writeUInt16LE(method, 8);
		header.writeUInt16LE(0, 10);
		header.writeUInt16LE(0x2100, 12);
		header.writeUInt32LE(crc32(data), 14);
		header.writeUInt32LE(body.length, 18);
		header.writeUInt32LE(data.length, 22);
		header.writeUInt16LE(nameBuf.length, 26);
		header.writeUInt16LE(0, 28);
		chunks.push(header, nameBuf, body);
		const entry = Buffer.alloc(46);
		entry.writeUInt32LE(0x02014b50, 0);
		entry.writeUInt16LE(20, 4);
		entry.writeUInt16LE(20, 6);
		entry.writeUInt16LE(0x0800, 8);
		entry.writeUInt16LE(method, 10);
		entry.writeUInt16LE(0, 12);
		entry.writeUInt16LE(0x2100, 14);
		entry.writeUInt32LE(crc32(data), 16);
		entry.writeUInt32LE(body.length, 20);
		entry.writeUInt32LE(data.length, 24);
		entry.writeUInt16LE(nameBuf.length, 28);
		entry.writeUInt32LE(offset, 42);
		central.push(entry, nameBuf);
		offset += 30 + nameBuf.length + body.length;
	}
	const centralBuf = Buffer.concat(central);
	const end = Buffer.alloc(22);
	end.writeUInt32LE(0x06054b50, 0);
	end.writeUInt16LE(files.length, 8);
	end.writeUInt16LE(files.length, 10);
	end.writeUInt32LE(centralBuf.length, 12);
	end.writeUInt32LE(offset, 16);
	return Buffer.concat([...chunks, centralBuf, end]);
};
const payload = collect(root).filter(({
	name
}) => !name.startsWith("vsix/"));
const runtime = payload.filter(({
	name
}) => !/^(test|test_original|tools)\//.test(name));
const types = `<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="mjs" ContentType="application/javascript"/>
  <Default Extension="css" ContentType="text/css"/>
  <Default Extension="html" ContentType="text/html"/>
  <Default Extension="svg" ContentType="image/svg+xml"/>
  <Default Extension="md" ContentType="text/markdown"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>
`;
const vsixManifest = `<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="${NAME}" Version="${VERSION}" Publisher="${manifest.publisher}"/>
    <DisplayName>${manifest.displayName}</DisplayName>
    <Description xml:space="preserve">${manifest.description}</Description>
    <Categories>${(manifest.categories||[]).join(",")}</Categories>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
`;
const vsixFiles = [{
	name: "[Content_Types].xml",
	data: Buffer.from(types)
}, {
	name: "extension.vsixmanifest",
	data: Buffer.from(vsixManifest)
}, ...runtime.map(({
	name,
	data
}) => ({
	name: `extension/${name}`,
	data
})), ];
mkdirSync(outDir, {
	recursive: true
});
const sourcePath = join(outDir, `${NAME}-${VERSION}-src.zip`);
const vsixPath = join(outDir, `${NAME}-${VERSION}.vsix`);
writeFileSync(sourcePath, zip(payload));
writeFileSync(vsixPath, zip(vsixFiles));
console.log(`package: ${basename(sourcePath)}, ${basename(vsixPath)} (${payload.length} source files)`);
