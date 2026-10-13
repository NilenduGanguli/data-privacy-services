import { describe, expect, it } from 'vitest';
import {
  columnLabel, connectionFields, connectionParams, filterModes, mongoFilterTemplate, needsTransactions, objectLabel, ownerLabel, parseMongoFilter,
  secretRefHelp, versionLabel,
} from './engines';

describe('connectionFields', () => {
  it('asks Oracle for a required service name and no database', () => {
    const keys = connectionFields('oracle').map((f) => f.key);
    expect(keys).toContain('service_name');
    expect(keys).not.toContain('database');
    expect(connectionFields('oracle').find((f) => f.key === 'service_name')?.required).toBe(true);
    expect(connectionFields('oracle').find((f) => f.key === 'port')?.defaultValue).toBe(1521);
  });
  it('asks MongoDB for a required database and the replica set', () => {
    const fields = connectionFields('mongo');
    expect(fields.find((f) => f.key === 'database')?.required).toBe(true);
    expect(fields.map((f) => f.key)).toContain('options.replicaSet');
    expect(fields.find((f) => f.key === 'port')?.defaultValue).toBe(27017);
  });
  it('keeps PostgreSQL as M2 had it', () => {
    expect(connectionFields('postgres').map((f) => f.key)).toEqual(['host', 'port', 'database', 'options.sslmode']);
  });
});

describe('connectionParams', () => {
  it('builds the params of each engine and names the first missing field', () => {
    expect(connectionParams('oracle', { host: 'ora.bank', port: '1521', service_name: 'FREEPDB1' })).toEqual({
      params: { host: 'ora.bank', port: 1521, service_name: 'FREEPDB1', options: {} }, problem: null,
    });
    expect(connectionParams('oracle', { host: 'ora.bank', port: '1521' }).problem).toMatch(/Service name/);
    expect(connectionParams('mongo', { host: 'mdb', port: '27017', database: 'crm', 'options.replicaSet': 'rs0', 'options.tls': '' })).toEqual({
      params: { host: 'mdb', port: 27017, database: 'crm', options: { replicaSet: 'rs0' } }, problem: null,
    });
    expect(connectionParams('postgres', { host: 'h', port: '99999', database: 'd' }).problem).toMatch(/Port/);
  });
  it('lets a secret dsn or uri stand in for the address on Oracle and MongoDB', () => {
    const out = connectionParams('mongo', { database: 'crm' }, { secretRef: true });
    expect(out.problem).toBeNull();
    expect(out.params).toEqual({ database: 'crm', options: {} });
    expect(connectionParams('oracle', {}, { secretRef: true }).problem).toBeNull();
    expect(connectionParams('mongo', {}, { secretRef: true }).problem).toMatch(/Database/);
    expect(connectionParams('postgres', { database: 'bank' }, { secretRef: true }).problem).toMatch(/Host/);
  });
});

describe('labels', () => {
  it('names owners, objects, filters and help per engine', () => {
    expect(ownerLabel('oracle')).toBe('Owner');
    expect(ownerLabel('mongo')).toBeNull();
    expect(objectLabel('mongo')).toBe('Collection');
    expect(objectLabel('postgres')).toBe('Table');
    expect(filterModes('mongo')).toEqual(['mongo']);
    expect(filterModes('oracle')).toEqual(['where', 'query']);
    expect(secretRefHelp('oracle')).toMatch(/dsn/);
    expect(secretRefHelp('mongo')).toMatch(/uri/);
    expect(versionLabel('oracle', '23.26.0.0')).toBe('Oracle Database 23.26.0.0');
    expect(versionLabel('mongo', '7.0.14')).toBe('MongoDB 7.0.14');
  });
  it('knows which write modes need transactions', () => {
    expect(needsTransactions('in_place')).toBe(true);
    expect(needsTransactions('append')).toBe(true);
    expect(needsTransactions('table')).toBe(false);
  });
});

describe('parseMongoFilter', () => {
  it('accepts a JSON object', () => {
    expect(parseMongoFilter('{"segment": "premium"}')).toEqual({ filter: { segment: 'premium' }, error: null });
    expect(parseMongoFilter('  ')).toEqual({ filter: null, error: null });
  });
  it('flags server-side JavaScript operators without echoing the filter', () => {
    const out = parseMongoFilter('{"$where": "1"}');
    expect(out.filter).toBeNull();
    expect(out.error).toContain('$where');
    expect(out.error).not.toContain('"1"');
    expect(parseMongoFilter('{"a": {"$elemMatch": {"$function": {"body": "x"}}}}').error).toContain('$function');
  });
  it('explains text that is not a JSON object', () => {
    expect(parseMongoFilter('nope').error).toBe('The filter is not valid JSON.');
    expect(parseMongoFilter('[1, 2]').error).toBe('The filter must be a JSON object, such as {"segment": "premium"}.');
    expect(parseMongoFilter('{"email": "a@x.in"').error).not.toContain('a@x.in');
  });
});

describe('mongoFilterTemplate', () => {
  it('replaces every literal with <bound>', () => {
    expect(mongoFilterTemplate({ a: 1, b: { $in: ['x', 'y'] } })).toEqual({ a: '<bound>', b: { $in: ['<bound>', '<bound>'] } });
    expect(mongoFilterTemplate({ $and: [{ c: null }, { d: { $exists: true } }] })).toEqual({ $and: [{ c: '<bound>' }, { d: { $exists: '<bound>' } }] });
  });
});

describe('columnLabel', () => {
  it('shows a document path without $. and a SQL column by name', () => {
    expect(columnLabel({ path: '$.profile.fullName', name: 'profile' })).toBe('profile.fullName');
    expect(columnLabel({ path: '$.accounts[*].iban', name: 'accounts' })).toBe('accounts[].iban');
    expect(columnLabel({ path: '$.customerId', name: 'customerId' })).toBe('customerId');
    expect(columnLabel({ path: '$.CUSTOMER_ID', name: 'CUSTOMER_ID' })).toBe('CUSTOMER_ID');
    expect(columnLabel({ path: 'email', name: 'email' })).toBe('email');
  });
});
