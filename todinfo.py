import numpy as np
from enlib import coordinates, utils, errors, bunch, tagdb
from enact import actdata, files
day_range = [11,23]
jon_ref   = 1378840304

# Implement our todinfo database using a Tagdb
class Todinfo(tagdb.Tagdb):
	def __init__(self, data, sort="id"):
		tagdb.Tagdb.__init__(self, data, sort=sort, default_fields=["sel",("t",np.NaN)], default_query="sel,isfinite(t)")
	def get_funcs(self):
		res = tagdb.Tagdb.get_funcs(self)
		# Define wrapper here for the default argument
		def hits(point, polys=None):
			if polys is None: polys = self.data["bounds"]
			return point_in_polygon_safe(point, polys)
		def dist(point, ref=None):
			if ref is None: ref=[self.data["ra"],self.data["dec"]]
			return utils.angdist(np.array(point)*utils.degree,np.array(ref)*utils.degree, zenith=False)/utils.degree
		def grow(polys, dist):
			return grow_polygon(polys*utils.degree, dist*utils.degree)/utils.degree
		res["hits"] = hits
		res["dist"] = dist
		res["grow"] = grow
		return res
	# Print the most useful fields + the true tags for each tod
	def __repr__(self, nmax=None):
		lines = []
		n = len(self)
		n1, n2 = (n, 0) if not nmax or n <= nmax else (nmax/4, nmax/4)
		finfo = [
				("id",  "%25s", "%25s"),
				("sel", "%3d",   "%3s"),
				("hour","%5.2f", "%5s"),
				("az",  "%7.2f", "%7s"),
				("el",  "%6.2f", "%6s"),
				("ra",  "%6.2f", "%6s"),
				("dec", "%6.2f", "%6s"),
				("pwv", "%5.2f", "%5s"),
				("wx",  "%6.2f", "%6s"),
				("wy",  "%6.2f", "%6s"),
		]
		# Prune so we can still print something if the normal fields are missing
		finfo = [fi for fi in finfo if fi[0] in self.data]
		fdata = [self.data[fi[0]] for fi in finfo]
		lfmt  = " " + " ".join([fi[1] for fi in finfo])
		hfmt  = "#" + " ".join([fi[2] for fi in finfo])
		hnames= tuple([fi[0] for fi in finfo])
		# Ok, generate the output lines
		header= hfmt % hnames + " tags"
		lines = [header]
		def pline(i):
			line = lfmt % tuple([fd[i] for fd in fdata])
			line += " " + " ".join(sorted([key for key,val in self.data.iteritems() if key != "id" and val.dtype == bool and val.ndim == 1 and val[i]]))
			return line
		for i in range(0,n1):
			lines.append(pline(i))
		if n2 > 0:
			lines.append("       ...       ")
			for i in range(n-n2, n):
				lines.append(pline(i))
		return "\n".join(lines)
	def __str__(self): return self.__repr__(100)
	@classmethod
	def read_txt(cls, fname):
		"""Read a Tagdb from text files, supporting Loic's selelected tod format"""
		datas = []
		for subfile, tags in tagdb.parse_tagfile_top(fname):
			data = parse_tagfile_loic(subfile)
			for tag in tags:
				data[tag] = np.full(len(data["id"]), True, dtype=bool)
			datas.append(data)
		return cls(tagdb.merge(datas))

def parse_tagfile_loic(fname):
	ids = []
	sel = []
	with open(fname,"r") as f:
		for line in f:
			line = line.rstrip()
			if len(line) < 1 or line[0] == "#": continue
			toks = line.split()
			# There are two formats
			if len(toks) >= 6:
				# 1. [id] [hour] [alt] [az] [pwv] [cut status] [[tag]]
				# Here a tod is only sel if the status is 2
				ids.append(toks[0])
				sel.append(int(toks[5]) == 2)
			elif len(toks) == 1:
				# 2. path to file. Here we will extract the id from
				# the path, and mark everything as *not* selectd.
				# So /all will be needed to access these.
				id = toks[0].split("/")[-1]
				if id.endswith(".zip"): id = id[:-4]
				ids.append(id)
				sel.append(False)
	ids = np.asarray(ids + ["foo"])[:-1]
	sel = np.asarray(sel)
	return {"id":ids, "sel":sel}

def read(fname, type=None):
	return Todinfo.read(fname, type)

# Functions that can be used in todinfo queries
# Coordinate order is ra,dec
def point_in_polygon_safe(points, polygons):
	points   = np.asarray(points)
	polygons = np.array(polygons)
	polygons[0] = utils.rewind(polygons[0], points[0], 360)
	return utils.point_in_polygon(points.T, polygons.T)
def grow_polygon(polys, dist):
	print "FIXME: grow_polygon is wrong"
	polys = np.array(polys)
	dist  = np.zeros(2) + dist
	# Compensate for curvature
	dist_eff = polys.copy()
	dist_eff[0] = dist[0] / np.cos(polys[1])
	dist_eff[1] = dist[1]
	# Expand away from center independently in each dimension
	mid = np.mean(polys,1)
	for i in range(2):
		polys[i,polys[i]<mid[i]] -= dist[i]
		polys[i,polys[i]>mid[i]] += dist[i]
	return polys

# Functions for extracting tod stats from tod files. Useful for building
# up Todinfos.
def build_tod_stats(entry, Naz=8, Nt=2):
	"""Collect summary information for the tod in the given entry, returning
	it as a bunch. If some information can't be found, then those fields will
	be set to a placeholder value (usually NaN), but the fields will still all
	be present."""
	# At the very least we need the pointing, so no try catch around this
	d = actdata.read(entry, ["boresight","site"])
	d += actdata.read_point_offsets(entry, no_correction=True)
	d = actdata.calibrate(d, exclude=["autocut"])

	# Get the array center and radius
	acenter = np.mean(d.point_offset,0) 
	arad    = np.mean((d.point_offset-acenter)**2,0)**0.5

	t, baz, bel = np.mean(d.boresight,1)
	az  = baz + acenter[0]
	el  = bel + acenter[1]
	dur, waz, wel = np.max(d.boresight,1)-np.min(d.boresight,1)
	mjd  = utils.ctime2mjd(t)
	hour = t/3600.%24
	day   = hour >= day_range[0] and hour < day_range[1]
	night = not day
	jon   = (t - jon_ref)/(3600*24)

	ra, dec = coordinates.transform("hor","cel",[az,el],mjd, site=d.site)
	# Get the array center bounds on the sky, assuming constant elevation
	ts  = utils.ctime2mjd(t+dur/2*np.linspace(-1,1,Nt))
	azs = az + waz/2*np.linspace(-1,1,Naz)
	E1 = coordinates.transform("hor","cel",[azs,         [el]*Naz],time=[ts[0]]*Naz, site=d.site)[:,1:]
	E2 = coordinates.transform("hor","cel",[[azs[-1]]*Nt,[el]*Nt], time=ts,          site=d.site)[:,1:]
	E3 = coordinates.transform("hor","cel",[azs[::-1],   [el]*Naz],time=[ts[-1]]*Naz,site=d.site)[:,1:]
	E4 = coordinates.transform("hor","cel",[[azs[0]]*Nt, [el]*Nt], time=ts[::-1],    site=d.site)[:,1:]
	bounds = np.concatenate([E1,E2,E3,E4],1)
	bounds[0] = utils.rewind(bounds[0])
	# Grow bounds by array radius
	bmid = np.mean(bounds,1)
	for i in range(2):
		bounds[i,bounds[i]<bmid[i]] -= arad[i]
		bounds[i,bounds[i]>bmid[i]] += arad[i]

	res = bunch.Bunch(id=entry.id, nsamp=d.nsamp, t=t, mjd=mjd, jon=jon,
			hour=hour, day=day, night=night,
			az =az /utils.degree,  el =el/utils.degree,
			baz=baz/utils.degree,  bel=bel/utils.degree,
			waz=waz/utils.degree,  wel=wel/utils.degree,
			ra =ra /utils.degree,  dec=dec/utils.degree,
			bounds = bounds/utils.degree)

	# Planets
	for obj in ["Sun","Moon","Mercury","Venus","Mars","Jupiter","Saturn","Uranus","Neptune"]:
		res[obj] = coordinates.ephem_pos(obj, utils.ctime2mjd(t))/utils.degree

	# Get our weather information, if available
	try:
		d += actdata.read(entry, ["apex"])
		d  = actdata.calibrate_apex(d)
		res["pwv"] = d.apex.pwv
		res["wx"] = d.apex.wind[0]
		res["wy"] = d.apex.wind[1]
		res["wind_speed"] = d.apex.wind_speed
		res["T"] = d.apex.temperature
	except errors.DataMissing:
		res["pwv"] = np.NaN
		res["wx"] = np.NaN
		res["wy"] = np.NaN
		res["wind_speed"] = np.NaN
		res["T"] = np.NaN
	
	# Try to get our cut info, so that we can select on
	# number of detectors and cut fraction
	try:
		npre = d.nsamp*d.ndet
		d += actdata.read(entry, ["cut"])
		res["ndet"] = d.ndet
		res["cut"] = 1-d.nsamp*d.ndet/float(npre)
	except errors.DataMissing:
		res["ndet"] = 0
		res["cut"] = 1.0

	# Try to get hwp info
	res["hwp"] = False
	res["hwp_name"] = "none"
	try:
		epochs = actdata.try_read(files.read_hwp_epochs, "hwp_epochs", entry.hwp_epochs)
		t, _, ar = entry.id.split(".")
		t = float(t)
		if ar in epochs:
			for epoch in epochs[ar]:
				if t >= epoch[0] and t < epoch[1]:
					res["hwp"] = True
					res["hwp_name"] = epoch[2]
	except errors.DataMissing:
		pass

	return res

def merge_tod_stats(statlist):
	return bunch.Bunch(**{key: np.array([stat[key] for stat in statlist]) for key in statlist[0]})

def get_tods(selector, db):
	try:
		return np.array(utils.read_lines(selector))
	except IOError:
		return db[selector]
