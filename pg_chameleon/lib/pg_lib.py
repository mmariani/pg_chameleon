import psycopg2
import os
import sys
import json
import datetime
import decimal
import time
import base64
class pg_encoder(json.JSONEncoder):
	def default(self, obj):
		if isinstance(obj, datetime.time) or isinstance(obj, datetime.datetime) or  isinstance(obj, datetime.date) or isinstance(obj, decimal.Decimal):
			return str(obj)
		return json.JSONEncoder.default(self, obj)


class pg_connection(object):
	def __init__(self, global_config):
		self.global_conf=global_config
		self.pg_conn=self.global_conf.pg_conn
		self.pg_database=self.global_conf.pg_database
		self.dest_schema=self.global_conf.my_database
		self.pg_connection=None
		self.pg_cursor=None
		self.pg_charset=self.global_conf.pg_charset
		
	
	def connect_db(self):
		pg_pars=dict(list(self.pg_conn.items())+ list({'dbname':self.pg_database}.items()))
		strconn="dbname=%(dbname)s user=%(user)s host=%(host)s password=%(password)s port=%(port)s"  % pg_pars
		self.pgsql_conn = psycopg2.connect(strconn)
		self.pgsql_conn .set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
		self.pgsql_conn .set_client_encoding(self.pg_charset)
		self.pgsql_cur=self.pgsql_conn .cursor()
		
	
	def disconnect_db(self):
		self.pgsql_conn.close()
		

class pg_engine(object):
	def __init__(self, global_config, table_metadata, table_file, logger, sql_dir='sql/'):
		self.sleep_on_reindex=global_config.sleep_on_reindex
		self.reindex_app_names=global_config.reindex_app_names
		self.logger=logger
		self.sql_dir=sql_dir
		self.idx_sequence=0
		self.pg_conn=pg_connection(global_config)
		self.pg_conn.connect_db()
		self.table_metadata=table_metadata
		self.table_file=table_file
		self.type_dictionary={
												'integer':'integer',
												'mediumint':'bigint',
												'tinyint':'integer',
												'smallint':'integer',
												'int':'integer',
												'bigint':'bigint',
												'varchar':'character varying',
												'text':'text',
												'char':'character',
												'datetime':'timestamp without time zone',
												'date':'date',
												'time':'time without time zone',
												'timestamp':'timestamp without time zone',
												'tinytext':'text',
												'mediumtext':'text',
												'longtext':'text',
												'tinyblob':'bytea',
												'mediumblob':'bytea',
												'longblob':'bytea',
												'blob':'bytea', 
												'binary':'bytea', 
												'decimal':'numeric', 
												'double':'double precision', 
												'double precision':'double precision', 
												'float':'float', 
												'bit':'integer', 
												'year':'integer', 
												'enum':'enum', 
												'set':'text', 
												'json':'text'
										}
		self.table_ddl={}
		self.idx_ddl={}
		self.type_ddl={}
		self.pg_charset=self.pg_conn.pg_charset
		self.cat_version='0.6'
		self.cat_sql=[
									{'version':'base','script': 'create_schema.sql'}, 
									{'version':'0.1','script': 'upgrade/cat_0.1.sql'}, 
									{'version':'0.2','script': 'upgrade/cat_0.2.sql'}, 
									{'version':'0.3','script': 'upgrade/cat_0.3.sql'}, 
									{'version':'0.4','script': 'upgrade/cat_0.4.sql'}, 
									{'version':'0.5','script': 'upgrade/cat_0.5.sql'}, 
									{'version':'0.6','script': 'upgrade/cat_0.6.sql'}, 
							]
		cat_version=self.get_schema_version()
		num_schema=(self.check_service_schema())[0]
		if cat_version!=self.cat_version and int(num_schema)>0:
			self.upgrade_service_schema()
	
			
		
	
	def create_schema(self):
		sql_drop="DROP SCHEMA IF EXISTS "+self.pg_conn.dest_schema+" CASCADE;"
		sql_create=" CREATE SCHEMA IF NOT EXISTS "+self.pg_conn.dest_schema+";"
		sql_path=" SET search_path="+self.pg_conn.dest_schema+";"
		self.pg_conn.pgsql_cur.execute(sql_drop)
		self.pg_conn.pgsql_cur.execute(sql_create)
		self.pg_conn.pgsql_cur.execute(sql_path)
	
	def store_table(self, table_name):
		table_data=self.table_metadata[table_name]
		for index in table_data["indices"]:
			if index["index_name"]=="PRIMARY":
				sql_insert=""" INSERT INTO sch_chameleon.t_replica_tables 
										(
											v_table_name,
											v_schema_name,
											v_table_pkey
										)
										VALUES (
														%s,
														%s,
														ARRAY[%s]
													)
										ON CONFLICT (v_table_name,v_schema_name)
											DO UPDATE 
												SET v_table_pkey=EXCLUDED.v_table_pkey
										;
								"""
				self.pg_conn.pgsql_cur.execute(sql_insert, (table_name, self.pg_conn.dest_schema, index["index_columns"].strip()))	
	
	def unregister_table(self, table_name):
		self.logger.info("unregistering table %s from the replica catalog" % (table_name,))
		sql_delete=""" DELETE FROM sch_chameleon.t_replica_tables 
									WHERE
											v_table_name=%s
										AND	v_schema_name=%s
								RETURNING i_id_table
								;
						"""
		self.pg_conn.pgsql_cur.execute(sql_delete, (table_name, self.pg_conn.dest_schema))	
		removed_id=self.pg_conn.pgsql_cur.fetchone()
		table_id=removed_id[0]
		self.logger.info("renaming table %s to %s_%s" % (table_name, table_name, table_id))
		sql_rename="""ALTER TABLE IF EXISTS "%s"."%s" rename to "%s_%s"; """ % (self.pg_conn.dest_schema, table_name, table_name, table_id)
		self.logger.debug(sql_rename)
		self.pg_conn.pgsql_cur.execute(sql_rename)	
	
	def create_tables(self):
		
			for table in self.table_ddl:
				#sql_drop='DROP TABLE IF EXISTS "'+table+'" CASCADE ;'
				#self.pg_conn.pgsql_cur.execute(sql_drop)
				try:
					ddl_enum=self.type_ddl[table]
					for sql_type in ddl_enum:
						self.pg_conn.pgsql_cur.execute(sql_type)
				except psycopg2.Error as e:
					self.logger.error("SQLCODE: %s SQLERROR: %s" % (e.pgcode, e.pgerror))
					self.logger.error(sql_type)
					
				sql_create=self.table_ddl[table]
				try:
					self.pg_conn.pgsql_cur.execute(sql_create)
				except psycopg2.Error as e:
					self.logger.error("SQLCODE: %s SQLERROR: %s" % (e.pgcode, e.pgerror))
					self.logger.error(sql_create)
				self.store_table(table)
	
	def create_indices(self):
		self.logger.info("creating the indices")
		for index in self.idx_ddl:
			idx_ddl= self.idx_ddl[index]
			for sql_idx in idx_ddl:
				
				self.pg_conn.pgsql_cur.execute(sql_idx)
	
	def copy_data(self, table,  csv_file,  my_tables={}):
		column_copy=[]
		for column in my_tables[table]["columns"]:
			column_copy.append('"'+column["column_name"]+'"')
		sql_copy="COPY "+'"'+table+'"'+" ("+','.join(column_copy)+") FROM STDIN WITH NULL 'NULL' CSV QUOTE '\"' DELIMITER',' ESCAPE '\"' ; "
		self.pg_conn.pgsql_cur.copy_expert(sql_copy,csv_file)
		
	def insert_data(self, table,  insert_data,  my_tables={}):
		column_copy=[]
		column_marker=[]
		
		for column in my_tables[table]["columns"]:
			column_copy.append('"'+column["column_name"]+'"')
			column_marker.append('%s')
		sql_head="INSERT INTO "+'"'+table+'"'+" ("+','.join(column_copy)+") VALUES ("+','.join(column_marker)+");"
		for data_row in insert_data:
			column_values=[]
			for column in my_tables[table]["columns"]:
				column_values.append(data_row[column["column_name"]])
			try:
				self.pg_conn.pgsql_cur.execute(sql_head,column_values)	
			except psycopg2.Error as e:
					self.logger.error("SQLCODE: %s SQLERROR: %s" % (e.pgcode, e.pgerror))
					self.logger.error(self.pg_conn.pgsql_cur.mogrify(sql_head,column_values))
	
	def build_idx_ddl(self):
		
		""" the function iterates over the list l_pkeys and builds a new list with the statements for pkeys """
		for table_name in self.table_metadata:
			table=self.table_metadata[table_name]
			
			table_name=table["name"]
			indices=table["indices"]
			table_idx=[]
			for index in indices:
				indx=index["index_name"]
				index_columns=index["index_columns"]
				non_unique=index["non_unique"]
				if indx=='PRIMARY':
					pkey_name="pk_"+table_name[0:20]+"_"+str(self.idx_sequence)
					pkey_def='ALTER TABLE "'+table_name+'" ADD CONSTRAINT "'+pkey_name+'" PRIMARY KEY ('+index_columns+') ;'
					table_idx.append(pkey_def)
				else:
					if non_unique==0:
						unique_key='UNIQUE'
					else:
						unique_key=''
					index_name='"idx_'+indx[0:20]+table_name[0:20]+"_"+str(self.idx_sequence)+'"'
					idx_def='CREATE '+unique_key+' INDEX '+ index_name+' ON "'+table_name+'" ('+index_columns+');'
					table_idx.append(idx_def)
				self.idx_sequence+=1
					
			self.idx_ddl[table_name]=table_idx

	def build_tab_ddl(self):
		""" the function iterates over the list l_tables and builds a new list with the statements for tables"""
		
		for table_name in self.table_metadata:
			table=self.table_metadata[table_name]
			columns=table["columns"]
			
			ddl_head="CREATE TABLE "+'"'+table["name"]+'" ('
			ddl_tail=");"
			ddl_columns=[]
			ddl_enum=[]
			for column in columns:
				if column["is_nullable"]=="NO":
					col_is_null="NOT NULL"
				else:
					col_is_null="NULL"
				column_type=self.type_dictionary[column["data_type"]]
				if column_type=="enum":
					enum_type="enum_"+table["name"]+"_"+column["column_name"]
					sql_drop_enum='DROP TYPE IF EXISTS '+enum_type+' CASCADE;'
					sql_create_enum="CREATE TYPE "+enum_type+" AS ENUM "+column["enum_list"]+";"
					ddl_enum.append(sql_drop_enum)
					ddl_enum.append(sql_create_enum)
					column_type=enum_type
				if column_type=="character varying" or column_type=="character":
					column_type=column_type+"("+str(column["character_maximum_length"])+")"
				if column_type=='numeric':
					column_type=column_type+"("+str(column["numeric_precision"])+","+str(column["numeric_scale"])+")"
				if column_type=='bit' or column_type=='float':
					column_type=column_type+"("+str(column["numeric_precision"])+")"
				if column["extra"]=="auto_increment":
					column_type="bigserial"
				ddl_columns.append('"'+column["column_name"]+'" '+column_type+" "+col_is_null )
			def_columns=str(',').join(ddl_columns)
			self.type_ddl[table["name"]]=ddl_enum
			self.table_ddl[table["name"]]=ddl_head+def_columns+ddl_tail
	


	
	def get_schema_version(self):
		"""
			Gets the service schema version.
		"""
		sql_check="""
							SELECT 
											t_version
							FROM 
											sch_chameleon.v_version 
							;
						"""
		try:
			self.pg_conn.pgsql_cur.execute(sql_check)
			value_check=self.pg_conn.pgsql_cur.fetchone()
			cat_version=value_check[0]
		except:
			cat_version='base'
		return cat_version
		
	def upgrade_service_schema(self):
		"""
			Upgrade the service schema to the latest version using the upgrade files
		"""
		
		self.logger.info("Upgrading the service schema")
		install_script=False
		cat_version=self.get_schema_version()
			
		for install in self.cat_sql:
				script_ver=install["version"]
				script_schema=install["script"]
				self.logger.info("script schema %s, detected schema version %s - install_script:%s " % (script_ver, cat_version, install_script))
				if install_script==True:
					self.logger.info("Installing file version %s" % (script_ver, ))
					file_schema=open(self.sql_dir+script_schema, 'rb')
					sql_schema=file_schema.read()
					file_schema.close()
					print("=================================================")
					self.pg_conn.pgsql_cur.execute(sql_schema)
				
				
				if script_ver==cat_version and not install_script:
					self.logger.info("enabling install script")
					install_script=True
					
	def check_service_schema(self):
		sql_check="""
								SELECT 
									count(*)
								FROM 
									information_schema.schemata  
								WHERE 
									schema_name='sch_chameleon'
						"""
			
		self.pg_conn.pgsql_cur.execute(sql_check)
		num_schema=self.pg_conn.pgsql_cur.fetchone()
		return num_schema
	
	def create_service_schema(self):
		
		num_schema=self.check_service_schema()
		if num_schema[0]==0:
			for install in self.cat_sql:
				script_ver=install["version"]
				script_schema=install["script"]
				if script_ver=='base':
					self.logger.info("Installing service schema %s" % (script_ver, ))
					file_schema=open(self.sql_dir+script_schema, 'rb')
					sql_schema=file_schema.read()
					file_schema.close()
					self.pg_conn.pgsql_cur.execute(sql_schema)
		else:
			self.logger.error("The service schema is already created")
			
		
	def drop_service_schema(self):
		file_schema=open(self.sql_dir+"drop_schema.sql", 'rb')
		sql_schema=file_schema.read()
		file_schema.close()
		self.pg_conn.pgsql_cur.execute(sql_schema)
	
	def save_master_status(self, master_status):
		next_batch_id=None
		sql_tab_log=""" 
							SELECT 
								CASE
									WHEN v_log_table='t_log_replica_2'
									THEN 
										't_log_replica_1'
									ELSE
										't_log_replica_2'
								END AS v_log_table
							FROM
								(
									(
									SELECT
											v_log_table,
											ts_created
											
									FROM
											sch_chameleon.t_replica_batch
									)
									UNION ALL
									(
										SELECT
											't_log_replica_2'  AS v_log_table,
											'1970-01-01'::timestamp as ts_created
									)
									ORDER BY 
										ts_created DESC
									LIMIT 1
								) tab
						;
					"""
		self.pg_conn.pgsql_cur.execute(sql_tab_log)
		results=self.pg_conn.pgsql_cur.fetchone()
		table_file=results[0]
		master_data=master_status[0]
		binlog_name=master_data["File"]
		binlog_position=master_data["Position"]
		self.logger.debug("master data: table file %s, log name: %s, log position: %s " % (table_file, binlog_name, binlog_position))
		sql_master="""
							INSERT INTO sch_chameleon.t_replica_batch
															(
																t_binlog_name, 
																i_binlog_position,
																v_log_table
															)
												VALUES (
																%s,
																%s,
																%s
															)
							ON CONFLICT DO NOTHING
							RETURNING i_id_batch
							;
						"""
		self.logger.info("saving master data")
		try:
			self.pg_conn.pgsql_cur.execute(sql_master, (binlog_name, binlog_position, table_file))
			results=self.pg_conn.pgsql_cur.fetchone()
			next_batch_id=results[0]
		except psycopg2.Error as e:
					self.logger.error("SQLCODE: %s SQLERROR: %s" % (e.pgcode, e.pgerror))
					self.logger.error(self.pg_conn.pgsql_cur.mogrify(sql_master, (binlog_name, binlog_position, table_file)))
		except:
			pass
		return next_batch_id
		
	def get_batch_data(self):
		sql_batch="""WITH t_created AS
						(
							SELECT 
								max(ts_created) AS ts_created
							FROM 
								sch_chameleon.t_replica_batch  
							WHERE 
											NOT b_processed
								AND 	NOT b_replayed
						)
					UPDATE sch_chameleon.t_replica_batch
						SET b_started=True
						FROM 
							t_created
						WHERE
							t_replica_batch.ts_created=t_created.ts_created
					RETURNING
						i_id_batch,
						t_binlog_name,
						i_binlog_position ,
						v_log_table
					;
					"""
		self.pg_conn.pgsql_cur.execute(sql_batch)
		return self.pg_conn.pgsql_cur.fetchall()
	
	def insert_batch(self,group_insert):
		self.logger.debug("starting insert loop")
		for row_data in group_insert:
			global_data=row_data["global_data"]
			event_data=row_data["event_data"]
			event_update=row_data["event_update"]
			log_table=global_data["log_table"]
			sql_insert="""
									INSERT INTO sch_chameleon."""+log_table+"""
									(
										i_id_batch, 
										v_table_name, 
										v_schema_name, 
										enm_binlog_event, 
										t_binlog_name, 
										i_binlog_position, 
										jsb_event_data,
										jsb_event_update
									)
									VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
									;						
							"""
			try:
				self.pg_conn.pgsql_cur.execute(sql_insert,(
																				global_data["batch_id"], 
																				global_data["table"],  
																				global_data["schema"], 
																				global_data["action"], 
																				global_data["binlog"], 
																				global_data["logpos"], 
																				json.dumps(event_data, cls=pg_encoder), 
																				json.dumps(event_update, cls=pg_encoder))
																	)
			except:
				self.logger.error("error when storing event data. saving the discarded row")
				self.save_discarded_row(row_data,global_data["batch_id"])
	
	def save_discarded_row(self,row_data,batch_id):
		print(str(row_data))
		b64_row=base64.b64encode(str(row_data))
		sql_save="""INSERT INTO sch_chameleon.t_discarded_rows(
											i_id_batch, 
											t_row_data
											)
						VALUES (%s,%s);
						"""
		self.pg_conn.pgsql_cur.execute(sql_save,(batch_id,b64_row))
	
	def write_batch(self, group_insert):
		self.logger.debug("writing replica batch data")
		insert_list=[]
		for row_data in group_insert:
			global_data=row_data["global_data"]
			event_data=row_data["event_data"]
			event_update=row_data["event_update"]
			log_table=global_data["log_table"]
			insert_list.append(self.pg_conn.pgsql_cur.mogrify("(%s,%s,%s,%s,%s,%s,%s,%s)", (
																									global_data["batch_id"], 
																									global_data["table"],  
																									global_data["schema"], 
																									global_data["action"], 
																									global_data["binlog"], 
																									global_data["logpos"], 
																									json.dumps(event_data, cls=pg_encoder), 
																									json.dumps(event_update, cls=pg_encoder)
																								)
																		)
											)
		sql_insert="""
								INSERT INTO sch_chameleon."""+log_table+"""
								(
									i_id_batch, 
									v_table_name, 
									v_schema_name, 
									enm_binlog_event, 
									t_binlog_name, 
									i_binlog_position, 
									jsb_event_data,
									jsb_event_update
								)
								VALUES
									"""+ b','.join(insert_list ).decode()+"""
						"""
		try:
			self.pg_conn.pgsql_cur.execute(sql_insert)
		except:
			self.logger.error("error when saving batch data, fallback to inserts")
			self.insert_batch(group_insert)
		
	def set_batch_processed(self, id_batch):
		self.logger.debug("updating batch %s to processed" % (id_batch, ))
		sql_update=""" UPDATE sch_chameleon.t_replica_batch
										SET
												b_processed=True,
												ts_processed=now()
								WHERE
										i_id_batch=%s
								;
							"""
		self.pg_conn.pgsql_cur.execute(sql_update, (id_batch, ))
		
	def process_batch(self, replica_batch_size):
		batch_loop=True
		sql_process="""SELECT sch_chameleon.fn_process_batch(%s);"""
		while batch_loop:
			self.pg_conn.pgsql_cur.execute(sql_process, (replica_batch_size, ))
			batch_result=self.pg_conn.pgsql_cur.fetchone()
			batch_loop=batch_result[0]
			self.logger.debug("Batch loop value %s" % (batch_loop))
			

	def build_alter_table(self, token):
		""" the function builds the alter table statement from the token idata"""
		alter_cmd=[]
		ddl_enum=[]
		query_cmd=token["command"]
		table_name=token["name"]
		for alter_dic in token["alter_cmd"]:
			if alter_dic["command"] == 'DROP':
				alter_cmd.append("%(command)s %(name)s CASCADE" % alter_dic)
			elif alter_dic["command"] == 'ADD':
				column_type=self.type_dictionary[alter_dic["type"]]
				if column_type=="enum":
					enum_name="enum_"+table_name+"_"+alter_dic["name"]
					column_type=enum_name
					sql_drop_enum='DROP TYPE IF EXISTS '+column_type+' CASCADE;'
					sql_create_enum="CREATE TYPE "+column_type+" AS ENUM ("+alter_dic["dimension"]+");"
					ddl_enum.append(sql_drop_enum)
					ddl_enum.append(sql_create_enum)
				if column_type=="character varying" or column_type=="character" or column_type=='numeric' or column_type=='bit' or column_type=='float':
						column_type=column_type+"("+str(alter_dic["dimension"])+")"
				alter_cmd.append("%s \"%s\" %s NULL" % (alter_dic["command"], alter_dic["name"], column_type))	
				
		query=' '.join(ddl_enum)+" "+query_cmd + ' '+ table_name+ ' ' +', '.join(alter_cmd)+" ;"
		return query

	def drop_primary_key(self, token):
		self.logger.info("dropping primary key for table %s" % (token["name"],))
		sql_gen="""
						SELECT  DISTINCT
							format('ALTER TABLE %%I.%%I DROP CONSTRAINT %%I;',
							table_schema,
							table_name,
							constraint_name
							)
						FROM 
							information_schema.key_column_usage 
						WHERE 
								table_schema=%s 
							AND table_name=%s;
					"""
		self.pg_conn.pgsql_cur.execute(sql_gen, (self.pg_conn.dest_schema, token["name"]))
		value_check=self.pg_conn.pgsql_cur.fetchone()
		if value_check:
			sql_drop=value_check[0]
			self.pg_conn.pgsql_cur.execute(sql_drop)
			self.unregister_table(token["name"])
		

	def gen_query(self, token):
		""" the function generates the ddl"""
		query=""
		
		if token["command"] =="DROP TABLE":
			query=" %(command)s \"%(name)s\";" % token
		elif token["command"] =="CREATE TABLE":
			table_metadata={}
			table_metadata["columns"]=token["columns"]
			table_metadata["name"]=token["name"]
			table_metadata["indices"]=token["indices"]
			self.table_metadata={}
			self.table_metadata[token["name"]]=table_metadata
			self.build_tab_ddl()
			self.build_idx_ddl()
			query_type=' '.join(self.type_ddl[token["name"]])
			query_table=self.table_ddl[token["name"]]
			query_idx=' '.join(self.idx_ddl[token["name"]])
			query=query_type+query_table+query_idx
			self.store_table(token["name"])
		elif token["command"] == "ALTER TABLE":
			query=self.build_alter_table(token)
		elif token["command"] == "DROP PRIMARY KEY":
			self.drop_primary_key(token)
		return query 


	def write_ddl(self, token, query_data):
		sql_path=" SET search_path="+self.pg_conn.dest_schema+";"
		pg_ddl=sql_path+self.gen_query(token)
		log_table=query_data["log_table"]
		insert_vals=(	query_data["batch_id"], 
								token["name"],  
								query_data["schema"], 
								query_data["binlog"], 
								query_data["logpos"], 
								pg_ddl
							)
		sql_insert="""
								INSERT INTO sch_chameleon."""+log_table+"""
								(
									i_id_batch, 
									v_table_name, 
									v_schema_name, 
									enm_binlog_event, 
									t_binlog_name, 
									i_binlog_position, 
									t_query
								)
								VALUES
								(
									%s,
									%s,
									%s,
									'ddl',
									%s,
									%s,
									%s
								)
						"""
		self.pg_conn.pgsql_cur.execute(sql_insert, insert_vals)
		
		
		
	def check_reindex(self):
		"""the function checks if there is any reindex running and holds for  the given number of seconds """
		sql_check="""SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND application_name = ANY(%s) ;"""
		while True:
			self.pg_conn.pgsql_cur.execute(sql_check, (self.reindex_app_names, ))
			reindex_tup = self.pg_conn.pgsql_cur.fetchone()
			reindex_cnt = reindex_tup[0]
			if reindex_cnt == 0:
				break;
			self.logger.info("reindex detected, sleeping %s second(s)" % (self.sleep_on_reindex,))
			time.sleep(self.sleep_on_reindex)












