export class ECSWorld {
  constructor() {
    this.next = 1;
    this.entities = new Set();
    this.components = new Map();
  }

  create() {
    const id = this.next++;
    this.entities.add(id);
    return id;
  }

  add(id, type, value) {
    if (!this.components.has(type)) this.components.set(type, new Map());
    this.components.get(type).set(id, value);
    return value;
  }

  get(id, type) {
    return this.components.get(type)?.get(id);
  }

  remove(id) {
    this.entities.delete(id);
    for (const map of this.components.values()) map.delete(id);
  }

  query(...types) {
    const first = this.components.get(types[0]);
    if (!first) return [];
    const out = [];
    for (const id of first.keys()) {
      if (types.every((t) => this.components.get(t)?.has(id))) out.push(id);
    }
    return out;
  }
}
