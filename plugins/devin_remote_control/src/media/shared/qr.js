"use strict";
/** QR code matrix builder. */
class QrCode {
	/** @type {readonly (readonly [number, number, number][] | null)[]} */
	static blocks = /** @type {readonly (readonly [number, number, number][] | null)[]} */ (Object.freeze([
		null,
		[
			[1, 26, 19]
		],
		[
			[1, 44, 34]
		],
		[
			[1, 70, 55]
		],
		[
			[1, 100, 80]
		],
		[
			[1, 134, 108]
		],
		[
			[2, 86, 68]
		],
		[
			[2, 98, 78]
		],
		[
			[2, 121, 97]
		],
		[
			[2, 146, 116]
		],
		[
			[2, 86, 68],
			[2, 87, 69]
		]
	]));
	/** @type {readonly (readonly number[] | null)[]} */
	static alignments = Object.freeze([
		null,
		[],
		[6, 18],
		[6, 22],
		[6, 26],
		[6, 30],
		[6, 34],
		[6, 22, 38],
		[6, 24, 42],
		[6, 26, 46],
		[6, 28, 50]
	]);
	/** @type {(text: string) => Uint8Array} */
	static utf8 = text => new TextEncoder().encode(text);
	/** @type {(version: number) => number} */
	static dataCodewords = version => (QrCode.blocks[version] ?? []).reduce((sum, [count, , data]) => sum + count * data, 0);
	/** @type {(bytes: Uint8Array) => number} */
	static versionFor = bytes => {
		for (let version = 1; version <= 10; version += 1) {
			const countBits = version < 10 ? 8 : 16;
			if (4 + countBits + bytes.length * 8 <= QrCode.dataCodewords(version) * 8) return version;
		}
		throw new RangeError("QR payload is too large for the embedded encoder.");
	};
	/** @type {(bits: number[], value: number, length: number) => void} */
	static appendBits = (bits, value, length) => {
		for (let index = length - 1; index >= 0; index -= 1) bits.push(value >>> index & 1);
	};
	/** @type {(bytes: Uint8Array, version: number) => number[]} */
	static dataBytes = (bytes, version) => {
		const capacity = QrCode.dataCodewords(version) * 8;
		/** @type {number[]} */
		const bits = [];
		QrCode.appendBits(bits, 0x4, 4);
		QrCode.appendBits(bits, bytes.length, version < 10 ? 8 : 16);
		bytes.forEach(byte => {
			QrCode.appendBits(bits, byte, 8);
		});
		QrCode.appendBits(bits, 0, Math.min(4, capacity - bits.length));
		while (bits.length % 8) bits.push(0);
		/** @type {number[]} */
		const data = [];
		for (let index = 0; index < bits.length; index += 8) data.push(bits.slice(index, index + 8).reduce((value, bit) => (value << 1) | bit, 0));
		for (let pad = 0; data.length < capacity / 8; pad += 1) data.push(pad % 2 ? 0x11 : 0xec);
		return data;
	};
	/** @type {() => { exp: Uint8Array, log: Uint8Array }} */
	static gfTables = () => {
		const exp = new Uint8Array(512);
		const log = new Uint8Array(256);
		let value = 1;
		for (let index = 0; index < 255; index += 1) {
			exp[index] = value;
			log[value] = index;
			value <<= 1;
			if (value & 0x100) value ^= 0x11d;
		}
		for (let index = 255; index < 512; index += 1) exp[index] = exp[index - 255] ?? 0;
		return {
			exp,
			log
		};
	};
	static gf = QrCode.gfTables();
	/** @type {(left: number, right: number) => number} */
	static multiply = (left, right) => left === 0 || right === 0 ? 0 : QrCode.gf.exp[(QrCode.gf.log[left] ?? 0) + (QrCode.gf.log[right] ?? 0)] ?? 0;
	/** @type {(degree: number) => number[]} */
	static generator = degree => {
		/** @type {number[]} */
		let polynomial = [1];
		for (let index = 0; index < degree; index += 1) {
			const next = /** @type {number[]} */ (Array(polynomial.length + 1).fill(0));
			polynomial.forEach((coefficient, position) => {
				next[position] = (next[position] ?? 0) ^ coefficient;
				next[position + 1] = (next[position + 1] ?? 0) ^ QrCode.multiply(coefficient, QrCode.gf.exp[index] ?? 0);
			});
			polynomial = next;
		}
		return polynomial;
	};
	/** @type {(data: number[], degree: number) => number[]} */
	static remainder = (data, degree) => {
		const divisor = QrCode.generator(degree);
		const result = /** @type {number[]} */ (Array(degree).fill(0));
		data.forEach(byte => {
			const factor = byte ^ (result[0] ?? 0);
			result.copyWithin(0, 1);
			result[degree - 1] = 0;
			for (let index = 0; index < degree; index += 1) result[index] = (result[index] ?? 0) ^ QrCode.multiply(divisor[index + 1] ?? 0, factor);
		});
		return result;
	};
	/** @type {(data: number[], version: number) => number[]} */
	static codewords = (data, version) => {
		/** @type {{ data: number[], ecc: number[] }[]} */
		const blocks = [];
		let offset = 0;
		(QrCode.blocks[version] ?? []).forEach(([count, total, dataCount]) => {
			for (let index = 0; index < count; index += 1) {
				const chunk = data.slice(offset, offset + dataCount);
				blocks.push({
					data: chunk,
					ecc: QrCode.remainder(chunk, total - dataCount)
				});
				offset += dataCount;
			}
		});
		/** @type {number[]} */
		const result = [];
		const maxData = Math.max(...blocks.map(block => block.data.length));
		const maxEcc = Math.max(...blocks.map(block => block.ecc.length));
		for (let index = 0; index < maxData; index += 1) blocks.forEach(block => {
			if (index < block.data.length) result.push(block.data[index] ?? 0);
		});
		for (let index = 0; index < maxEcc; index += 1) blocks.forEach(block => {
			if (index < block.ecc.length) result.push(block.ecc[index] ?? 0);
		});
		return result;
	};
	/** @type {(mask: number) => number} */
	static formatBits = mask => {
		const data = 1 << 3 | mask;
		let remainder = data << 10;
		for (let bit = 14; bit >= 10; bit -= 1) {
			if (remainder >>> bit & 1) remainder ^= 0x537 << bit - 10;
		}
		return (data << 10 | remainder) ^ 0x5412;
	};
	/** @type {(version: number) => number} */
	static versionBits = version => {
		let remainder = version << 12;
		for (let bit = 17; bit >= 12; bit -= 1) {
			if (remainder >>> bit & 1) remainder ^= 0x1f25 << bit - 12;
		}
		return version << 12 | remainder;
	};
	/** @type {(mask: number, x: number, y: number) => boolean} */
	static maskBit = (mask, x, y) => [
		(x + y) % 2 === 0,
		y % 2 === 0,
		x % 3 === 0,
		(x + y) % 3 === 0,
		(Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0,
		x * y % 2 + x * y % 3 === 0,
		(x * y % 2 + x * y % 3) % 2 === 0,
		((x + y) % 2 + x * y % 3) % 2 === 0
	][mask] ?? false;
	/** @type {(matrix: boolean[][]) => number} */
	static penalty = matrix => {
		const size = matrix.length;
		let score = 0;
		/** @type {(line: boolean[]) => number} */
		const linePenalty = line => {
			let subtotal = 0;
			let run = 1;
			for (let index = 1; index < line.length; index += 1) {
				if (line[index] === line[index - 1]) run += 1;
				else {
					if (run >= 5) subtotal += 3 + run - 5;
					run = 1;
				}
			}
			if (run >= 5) subtotal += 3 + run - 5;
			const text = line.map(value => value ? "1" : "0").join("");
			for (let index = 0; index <= text.length - 11; index += 1) {
				const sample = text.slice(index, index + 11);
				if (sample === "10111010000" || sample === "00001011101") subtotal += 40;
			}
			return subtotal;
		};
		for (let index = 0; index < size; index += 1) {
			score += linePenalty(matrix[index] ?? []);
			score += linePenalty(matrix.map(row => row[index] ?? false));
		}
		for (let y = 0; y < size - 1; y += 1)
			for (let x = 0; x < size - 1; x += 1) {
				const row = matrix[y] ?? [];
				const below = matrix[y + 1] ?? [];
				const value = row[x];
				if (row[x + 1] === value && below[x] === value && below[x + 1] === value) score += 3;
			}
		const dark = matrix.flat().filter(Boolean).length;
		return score + Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10;
	};
	/**
	 * Creates an instance of.
	 * @param {number} version Version.
	 * @param {number[]} codewords Codewords.
	 */
	constructor(version, codewords) {
		/** @type {number} */
		this.version = version;
		/** @type {number} */
		this.size = version * 4 + 17;
		/** @type {boolean[][]} */
		this.modules = Array.from({
			length: this.size
		}, () => /** @type {boolean[]} */ (Array(this.size).fill(false)));
		/** @type {boolean[][]} */
		this.functions = Array.from({
			length: this.size
		}, () => /** @type {boolean[]} */ (Array(this.size).fill(false)));
		/** @type {number[]} */
		this.codewords = codewords;
		this.drawFunctions();
	}
	/**
	 * Sets function.
	 * @param {number} x X.
	 * @param {number} y Y.
	 * @param {boolean} value Value.
	 */
	setFunction(x, y, value) {
		if (x < 0 || y < 0 || x >= this.size || y >= this.size) return;
		const modules = this.modules[y];
		const functions = this.functions[y];
		if (!modules || !functions) return;
		modules[x] = value;
		functions[x] = true;
	}
	/**
	 * Sets module.
	 * @param {number} x X.
	 * @param {number} y Y.
	 * @param {boolean} value Value.
	 */
	setModule(x, y, value) {
		const row = this.modules[y];
		if (row) row[x] = value;
	}
	/**
	 * Returns whether the module is already a function element.
	 * @param {number} x X.
	 * @param {number} y Y.
	 * @returns {boolean} The resulting value.
	 */
	isFunction(x, y) {
		return (this.functions[y] ?? [])[x] ?? false;
	}
	/**
	 * Draws finder.
	 * @param {number} x X.
	 * @param {number} y Y.
	 */
	drawFinder(x, y) {
		for (let dy = -1; dy <= 7; dy += 1)
			for (let dx = -1; dx <= 7; dx += 1) {
				const inside = dx >= 0 && dx <= 6 && dy >= 0 && dy <= 6;
				const dark = inside && (dx === 0 || dx === 6 || dy === 0 || dy === 6 || (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4));
				this.setFunction(x + dx, y + dy, dark);
			}
	}
	/**
	 * Draws alignment.
	 * @param {number} cx Cx.
	 * @param {number} cy Cy.
	 */
	drawAlignment(cx, cy) {
		for (let dy = -2; dy <= 2; dy += 1)
			for (let dx = -2; dx <= 2; dx += 1) this.setFunction(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
	}
	/** Draws functions. */
	drawFunctions() {
		this.drawFinder(0, 0);
		this.drawFinder(this.size - 7, 0);
		this.drawFinder(0, this.size - 7);
		for (let index = 8; index < this.size - 8; index += 1) {
			this.setFunction(index, 6, index % 2 === 0);
			this.setFunction(6, index, index % 2 === 0);
		}
		const anchors = /** @type {readonly number[]} */ (QrCode.alignments[this.version] ?? []);
		anchors.forEach(cx => {
			anchors.forEach(cy => {
				if (!this.isFunction(cx, cy)) this.drawAlignment(cx, cy);
			});
		});
		for (let index = 0; index <= 5; index += 1) this.setFunction(8, index, false);
		this.setFunction(8, 7, false);
		this.setFunction(8, 8, false);
		this.setFunction(7, 8, false);
		for (let index = 9; index < 15; index += 1) this.setFunction(14 - index, 8, false);
		for (let index = 0; index < 8; index += 1) this.setFunction(this.size - 1 - index, 8, false);
		for (let index = 8; index < 15; index += 1) this.setFunction(8, this.size - 15 + index, false);
		this.setFunction(8, this.size - 8, true);
		if (this.version >= 7)
			for (let index = 0; index < 18; index += 1) {
				const a = this.size - 11 + index % 3;
				const b = Math.floor(index / 3);
				this.setFunction(a, b, false);
				this.setFunction(b, a, false);
			}
	}
	/** Places data. */
	placeData() {
		/** @type {number[]} */
		const bits = [];
		this.codewords.forEach(byte => {
			QrCode.appendBits(bits, byte, 8);
		});
		let bit = 0;
		let upward = true;
		for (let right = this.size - 1; right >= 1; right -= 2) {
			if (right === 6) right -= 1;
			for (let vertical = 0; vertical < this.size; vertical += 1) {
				const y = upward ? this.size - 1 - vertical : vertical;
				for (let offset = 0; offset < 2; offset += 1) {
					const x = right - offset;
					if (!this.isFunction(x, y)) this.setModule(x, y, bit < bits.length ? Boolean(bits[bit++]) : false);
				}
			}
			upward = !upward;
		}
	}
	/**
	 * Applies mask.
	 * @param {number} mask Mask.
	 */
	applyMask(mask) {
		for (let y = 0; y < this.size; y += 1)
			for (let x = 0; x < this.size; x += 1) {
				const row = this.modules[y];
				if (row && !this.isFunction(x, y) && QrCode.maskBit(mask, x, y)) row[x] = !row[x];
			}
	}
	/**
	 * Draws format.
	 * @param {number} mask Mask.
	 */
	drawFormat(mask) {
		const bits = QrCode.formatBits(mask);
		/** @type {(index: number) => boolean} */
		const bit = index => Boolean(bits >>> index & 1);
		for (let index = 0; index <= 5; index += 1) this.setModule(8, index, bit(index));
		this.setModule(8, 7, bit(6));
		this.setModule(8, 8, bit(7));
		this.setModule(7, 8, bit(8));
		for (let index = 9; index < 15; index += 1) this.setModule(14 - index, 8, bit(index));
		for (let index = 0; index < 8; index += 1) this.setModule(this.size - 1 - index, 8, bit(index));
		for (let index = 8; index < 15; index += 1) this.setModule(8, this.size - 15 + index, bit(index));
		this.setModule(8, this.size - 8, true);
	}
	/** Draws version. */
	drawVersion() {
		if (this.version < 7) return;
		const bits = QrCode.versionBits(this.version);
		for (let index = 0; index < 18; index += 1) {
			const value = Boolean(bits >>> index & 1);
			const a = this.size - 11 + index % 3;
			const b = Math.floor(index / 3);
			this.setModule(a, b, value);
			this.setModule(b, a, value);
		}
	}
	/**
	 * Best matrix.
	 * @returns {boolean[][] | null} The resulting value.
	 */
	bestMatrix() {
		this.placeData();
		/** @type {boolean[][] | null} */
		let best = null;
		let bestScore = Number.POSITIVE_INFINITY;
		for (let mask = 0; mask < 8; mask += 1) {
			const candidate = this.modules.map(row => [...row]);
			this.applyMask(mask);
			this.drawFormat(mask);
			this.drawVersion();
			const score = QrCode.penalty(this.modules);
			if (score < bestScore) {
				bestScore = score;
				best = this.modules.map(row => [...row]);
			}
			this.modules = candidate;
		}
		return best;
	}
	/** @type {(text: string) => boolean[][] | null} */
	static encode = text => {
		const bytes = QrCode.utf8(text);
		const version = QrCode.versionFor(bytes);
		return new QrCode(version, QrCode.codewords(QrCode.dataBytes(bytes, version), version)).bestMatrix();
	};
}
/** SVG renderer for QR matrices. */
class QrSvg {
	/** @type {(text: string, border?: number) => string} */
	static render = (text, border = 4) => {
		const matrix = QrCode.encode(text) ?? [];
		const size = matrix.length + border * 2;
		/** @type {string[]} */
		const path = [];
		matrix.forEach((row, y) => {
			row.forEach((dark, x) => {
				if (dark) path.push(`M${x + border},${y + border}h1v1h-1z`);
			});
		});
		return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="QR code"><rect width="100%" height="100%" fill="white"/><path d="${path.join("")}" fill="black"/></svg>`;
	};
}
if (typeof module !== "undefined" && module.exports) module.exports = {
	QrCode,
	QrSvg
};
else( /** @type {any} */ (globalThis)).QrSvg = QrSvg;
