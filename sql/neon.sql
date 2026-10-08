WITH por_anuncio AS (
    SELECT "Marca", "Advertisement",
           COUNT(*)           AS repeticiones,
           SUM("Impresiones") AS impresiones
    FROM anuncios
    WHERE "Marca" IS NOT NULL AND "Impresiones" IS NOT NULL
    GROUP BY "Marca", "Advertisement"
), ranking AS (
    SELECT por_anuncio.*,
           ROW_NUMBER() OVER (PARTITION BY "Marca"
                              ORDER BY impresiones DESC, repeticiones DESC, "Advertisement") AS posicion
    FROM por_anuncio
)
SELECT "Marca", "Advertisement", impresiones, repeticiones, posicion
FROM ranking
WHERE posicion <= 3
ORDER BY "Marca", posicion;
